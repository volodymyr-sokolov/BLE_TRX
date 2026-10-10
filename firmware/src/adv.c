/*
 * RADIO set-up and the advertising (TX) scheduler.
 *
 * Each advertising event sends one PDU on every configured channel in order,
 * `gap_us` apart, then waits interval (+ advDelay).  A whole event is sent by
 * one persona, so the second ("twin") persona stands in for a separate radio
 * seen under the same address -- the case the sniffer's clustering has to
 * resolve -- rather than splitting single events.
 *
 * TX runs the RADIO in polled mode from a dedicated thread: a legacy
 * advertising PDU is at most 376 us on air (a raw 257-byte PDU ~2.1 ms), so an
 * interrupt state machine would buy nothing.  The receiver (rx.c) shares the
 * radio; `radio_lock` is held by whichever side is configuring or using it,
 * and starting one side stops the other.
 */
#include "sim.h"

#include <string.h>
#include <nrfx.h>
#include <zephyr/random/random.h>
#include <zephyr/drivers/clock_control.h>
#include <zephyr/drivers/clock_control/nrf_clock_control.h>

/* PCNF1.MAXLEN is an 8-bit field.  258 would overflow it to 2 (and corrupt
 * STATLEN), truncating every packet to two payload bytes -- the bug that made
 * only the first four bytes of each PDU decode.  A legacy advertising payload
 * never exceeds 37 bytes, and 255 is the field's maximum. */
#define RADIO_MAXLEN 255u

static uint8_t pdu[MAX_RAW + 5] __aligned(4);
static struct k_thread adv_thr;
static K_SEM_DEFINE(adv_wake, 0, 1);

volatile struct sim_stats stats;
K_MUTEX_DEFINE(radio_lock);

/* Advertising-channel centre frequencies; data channels follow the BLE map. */
uint32_t channel_mhz(uint8_t ch)
{
	if (ch == 37) {
		return 2402;
	}
	if (ch == 38) {
		return 2426;
	}
	if (ch == 39) {
		return 2480;
	}
	if (ch <= 10) {
		return 2404 + 2 * ch;              /* data channels 0..10  */
	}
	return 2428 + 2 * (ch - 11);              /* data channels 11..36 */
}

static uint8_t freq_reg(uint8_t ch)
{
	return (uint8_t)(channel_mhz(ch) - 2400);
}

const char *phy_name(uint8_t phy)
{
	return phy == PHY_NRF1M ? "nrf1m" : "ble1m";
}

void radio_set_channel(uint8_t ch)
{
	NRF_RADIO->FREQUENCY = freq_reg(ch);
	/* BLE whitening LFSR init is {1, ch5..ch0}: position 0 is a hard 1,
	 * which the nRF takes from DATAWHITEIV bit 6.  Writing the channel index
	 * alone leaves bit 6 clear, so the first ~4 bytes de-whiten correctly
	 * (they come from the channel bits) and everything after diverges once
	 * the wrong seed bit propagates -- the exact symptom seen on air. */
	NRF_RADIO->DATAWHITEIV = 0x40u | (ch & 0x3F);
}

void radio_set_access_address(uint32_t aa)
{
	NRF_RADIO->BASE0 = (aa << 8) & 0xFFFFFF00u;
	NRF_RADIO->PREFIX0 = (aa >> 24) & 0xFFu;
}

void radio_disable(void)
{
	NRF_RADIO->SHORTS = 0;
	if (NRF_RADIO->STATE != RADIO_STATE_STATE_Disabled) {
		NRF_RADIO->EVENTS_DISABLED = 0;
		NRF_RADIO->TASKS_DISABLE = 1;
		while (NRF_RADIO->EVENTS_DISABLED == 0) {
			/* a few microseconds */
		}
	}
	NRF_RADIO->EVENTS_DISABLED = 0;
}

/* The twin's lower deviation (Nordic proprietary 1M, ~170 kHz) gives it a
 * modulation index near 0.34 against BLE 1M's 0.5, so the two personas carry a
 * physically different modulation index -- the primary fingerprint feature. */
void radio_set_phy(uint8_t phy)
{
	NRF_RADIO->MODE = (phy == PHY_NRF1M)
		? (RADIO_MODE_MODE_Nrf_1Mbit << RADIO_MODE_MODE_Pos)
		: (RADIO_MODE_MODE_Ble_1Mbit << RADIO_MODE_MODE_Pos);
}

int adv_power_supported(int dbm)
{
	static const int8_t tbl[] = {8, 7, 6, 5, 4, 3, 2, 0, -4, -8, -12, -16, -20, -40};

	for (size_t i = 0; i < ARRAY_SIZE(tbl); i++) {
		if (dbm == tbl[i]) {
			return 1;
		}
	}
	return 0;
}

static void hfxo_start(void)
{
	/* The crystal, not the RC oscillator: carrier-offset fingerprinting is
	 * the whole point of the receiver, so the source must be crystal-clean
	 * rather than drifting tens of ppm with the internal RC. */
	const struct device *clk = DEVICE_DT_GET_ONE(nordic_nrf_clock);

	if (device_is_ready(clk)) {
		struct onoff_manager *mgr =
			z_nrf_clock_control_get_onoff(CLOCK_CONTROL_NRF_SUBSYS_HF);
		struct onoff_client cli;

		sys_notify_init_spinwait(&cli.notify);
		if (onoff_request(mgr, &cli) >= 0) {
			int res;

			while (sys_notify_fetch_result(&cli.notify, &res) != 0) {
				k_busy_wait(50);
			}
		}
	}
}

void adv_init(void)
{
	hfxo_start();

	NRF_RADIO->POWER = 0;
	NRF_RADIO->POWER = 1;

	/* Fast ramp-up; DTX centred so an idle carrier does not sit off-channel. */
	NRF_RADIO->MODECNF0 = (RADIO_MODECNF0_RU_Fast << RADIO_MODECNF0_RU_Pos) |
			      (RADIO_MODECNF0_DTX_Center << RADIO_MODECNF0_DTX_Pos);

	NRF_RADIO->TXADDRESS = 0;
	NRF_RADIO->RXADDRESSES = 1u;        /* logical address 0 = BASE0/PREFIX0.AP0 */
	NRF_RADIO->PCNF0 = (1u << RADIO_PCNF0_S0LEN_Pos) |
			   (8u << RADIO_PCNF0_LFLEN_Pos) |
			   (0u << RADIO_PCNF0_S1LEN_Pos);
	/* 3 base + 1 prefix = a 4-byte access address, little-endian payload,
	 * hardware whitening on.  Leaving PCNF1 at reset gives BALEN=0 (a
	 * 1-byte access address the sniffer can never correlate), WHITEEN=0 and
	 * MAXLEN=0 -- every symptom of "transmits but is never decoded". */
	NRF_RADIO->PCNF1 = ((uint32_t)RADIO_MAXLEN << RADIO_PCNF1_MAXLEN_Pos) |
			   (3u << RADIO_PCNF1_BALEN_Pos) |
			   (RADIO_PCNF1_ENDIAN_Little << RADIO_PCNF1_ENDIAN_Pos) |
			   (1u << RADIO_PCNF1_WHITEEN_Pos);
	NRF_RADIO->CRCCNF = (RADIO_CRCCNF_LEN_Three << RADIO_CRCCNF_LEN_Pos) |
			    (RADIO_CRCCNF_SKIPADDR_Skip << RADIO_CRCCNF_SKIPADDR_Pos);
	NRF_RADIO->CRCPOLY = 0x100065Bu;   /* BLE CRC-24 */
	NRF_RADIO->PACKETPTR = (uint32_t)pdu;
}

/* Raw mode: the operator's bytes, verbatim.  The radio sends the 2-byte
 * header and then as many payload bytes as the header's LENGTH field says, so
 * a LENGTH larger than the bytes supplied goes out zero-padded (the host tool
 * normally makes the two agree before it gets here). */
static uint16_t build_raw(void)
{
	memset(pdu, 0, sizeof(pdu));
	memcpy(pdu, cfg.raw, cfg.raw_len);
	return 2 + pdu[1];
}

/* Build one legacy advertising PDU into `pdu`.  header, length, then payload.
 * p->addr is already in on-air (LSB-first) order. */
static uint16_t build_pdu(const struct persona *p, uint16_t counter)
{
	uint8_t hdr = cfg.pdu_type & 0x0F;

	if (p->addr_random) {
		hdr |= 1u << 6;                /* TxAdd = random */
	}
	if (cfg.pdu_type == 0x01 && cfg.target_random) {
		hdr |= 1u << 7;                /* RxAdd for ADV_DIRECT_IND */
	}

	uint8_t *payload = &pdu[2];
	uint16_t n = 0;

	memcpy(&payload[n], p->addr, 6);
	n += 6;

	if (cfg.pdu_type == 0x01) {            /* ADV_DIRECT_IND: AdvA + TargetA */
		memcpy(&payload[n], cfg.target, 6);
		n += 6;
	} else {
		uint16_t dlen = cfg.adv_len;

		if (dlen > MAX_ADV_DATA) {
			dlen = MAX_ADV_DATA;
		}
		memcpy(&payload[n], cfg.adv_data, dlen);
		n += dlen;
		if (cfg.counter_on && n + 4 <= MAX_ADV_DATA) {
			payload[n++] = 3;      /* AD length */
			payload[n++] = 0xFF;   /* manufacturer-specific */
			payload[n++] = counter & 0xFF;
			payload[n++] = counter >> 8;
		}
	}

	pdu[0] = hdr;
	pdu[1] = (uint8_t)n;                   /* payload length */
	return n + 2;
}

static void send_on(uint8_t ch, bool corrupt)
{
	radio_set_channel(ch);
	NRF_RADIO->PACKETPTR = (uint32_t)pdu;   /* the receiver uses its own buffers */
	NRF_RADIO->CRCINIT = cfg.crc_init & 0xFFFFFF;
	if (corrupt) {
		/* Deliberately wrong seed -> the sniffer must see a CRC failure
		 * and colour the row, without the PDU being otherwise malformed. */
		NRF_RADIO->CRCINIT = (cfg.crc_init ^ 0x5A5A5Au) & 0xFFFFFF;
	}

	NRF_RADIO->EVENTS_END = 0;
	NRF_RADIO->EVENTS_DISABLED = 0;
	NRF_RADIO->SHORTS = RADIO_SHORTS_READY_START_Msk |
			    RADIO_SHORTS_END_DISABLE_Msk;
	NRF_RADIO->TASKS_TXEN = 1;
	while (NRF_RADIO->EVENTS_DISABLED == 0) {
		/* ~100-400 us; polling is simplest and nothing else runs here */
	}
}


static void adv_thread(void *a, void *b, void *c)
{
	ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);

	for (;;) {
		if (!stats.running) {
			k_sem_take(&adv_wake, K_FOREVER);
			continue;
		}

		k_mutex_lock(&cfg_lock, K_FOREVER);

		bool use_twin = cfg.twin_on && cfg.twin_pct > 0 &&
				(sys_rand32_get() % 100) < cfg.twin_pct;
		const struct persona *p = use_twin ? &cfg.twin : &cfg.main;
		struct persona chosen = *p;

		if (use_twin && cfg.twin_same_addr) {
			memcpy(chosen.addr, cfg.main.addr, 6);
			chosen.addr_random = cfg.main.addr_random;
		}
		bool corrupt = cfg.crc_err_pct > 0 &&
			       (sys_rand32_get() % 100) < cfg.crc_err_pct;
		uint16_t counter = stats.counter;
		uint8_t nch = cfg.n_channels;
		uint8_t chans[MAX_CHANNELS];
		uint16_t gap = cfg.gap_us;
		uint16_t interval = cfg.interval_ms;
		bool delay = cfg.adv_delay;

		memcpy(chans, cfg.channels, nch);

		/* The receiver may have been started since this event was
		 * scheduled; it stops TX before it takes the radio, so re-check
		 * under the lock rather than transmit over it. */
		k_mutex_lock(&radio_lock, K_FOREVER);
		if (!stats.running) {
			k_mutex_unlock(&radio_lock);
			k_mutex_unlock(&cfg_lock);
			continue;
		}
		radio_set_access_address(cfg.access_address);
		radio_set_phy(chosen.phy);
		NRF_RADIO->TXPOWER = (uint8_t)chosen.power_dbm;
		if (cfg.raw_on && cfg.raw_len >= 2) {
			build_raw();
		} else {
			build_pdu(&chosen, counter);
		}

		k_mutex_unlock(&cfg_lock);

		for (uint8_t i = 0; i < nch; i++) {
			send_on(chans[i], corrupt);
			stats.packets++;
			if (i + 1 < nch && gap) {
				k_busy_wait(gap);
			}
		}
		k_mutex_unlock(&radio_lock);

		stats.events++;
		stats.counter++;
		if (use_twin) {
			stats.twin_events++;
		}
		if (corrupt) {
			stats.crc_errors++;
		}

		if (stats.remaining) {
			if (--stats.remaining == 0) {
				stats.running = false;
				hostif_printf("{\"ev\":\"done\",\"events\":%u}", stats.events);
				continue;
			}
		}

		uint32_t wait = interval;

		if (delay) {
			wait += sys_rand32_get() % 11;   /* spec advDelay 0-10 ms */
		}
		k_msleep(wait ? wait : 1);
	}
}

K_THREAD_STACK_DEFINE(adv_stack, 2048);

void adv_start(uint32_t count)
{
	rx_stop();                     /* one radio: TX and RX are exclusive */
	stats.remaining = count;       /* 0 = run until stopped */
	stats.running = true;
	k_sem_give(&adv_wake);
}

void adv_stop(void)
{
	stats.running = false;
	/* Wait out an event already on air, so the caller owns an idle radio. */
	k_mutex_lock(&radio_lock, K_FOREVER);
	k_mutex_unlock(&radio_lock);
}

static int adv_sys_init(void)
{
	k_thread_create(&adv_thr, adv_stack, K_THREAD_STACK_SIZEOF(adv_stack),
			adv_thread, NULL, NULL, NULL, 7, 0, K_NO_WAIT);
	k_thread_name_set(&adv_thr, "adv");
	return 0;
}
SYS_INIT(adv_sys_init, APPLICATION, 90);
