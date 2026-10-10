/*
 * The receiver: a BLE link-layer sniffer on one channel or a hop list.
 *
 * The RADIO runs interrupt-driven.  On every END the ISR copies the PDU, its
 * CRC verdict, the received CRC, RSSI and an address-match timestamp into a
 * queue and restarts reception straight away; a reporter thread turns the
 * queue into one JSON line per packet on the USB port.  Nothing on the host
 * side can stall the radio: if the host does not keep up, the queue fills and
 * the ISR counts the packets it had to drop (`rx info` reports them).
 *
 * Timestamps: RADIO EVENTS_ADDRESS (the end of the access address) captures
 * TIMER0, free-running at 1 MHz, through the pre-programmed PPI channel 26, so
 * the time is taken in hardware at the same point of every packet and is not
 * blurred by interrupt latency.  The 32-bit count is widened to 64 bits here.
 */
#include "sim.h"

#include <string.h>
#include <nrfx.h>
#include <zephyr/irq.h>

#define RX_QUEUE_LEN 24
/* Fixed PPI channel 26: RADIO->EVENTS_ADDRESS -> TIMER0->TASKS_CAPTURE[1]. */
#define PPI_CH_ADDRESS_TO_TIMER0 26

struct rx_pkt {
	uint32_t t_us;          /* TIMER0 at the access-address match */
	uint32_t crc;           /* RXCRC: CRC as received */
	uint8_t ch;
	uint8_t rssi;           /* RSSISAMPLE: -dBm */
	uint8_t crc_ok;
	uint8_t pad;
	uint32_t aa;            /* access address the packet was received on */
	uint8_t pdu[MAX_RAW];   /* header + payload; length is pdu[1] + 2 */
};

K_MSGQ_DEFINE(rxq, sizeof(struct rx_pkt), RX_QUEUE_LEN, 4);
static K_SEM_DEFINE(rx_wake, 0, 1);

volatile struct rx_stats rxstats;

static uint8_t rxbuf[MAX_RAW + 3] __aligned(4);
static struct rx_cfg active;     /* snapshot taken by rx_start() */
static volatile uint8_t cur_ch;
static volatile uint32_t cur_aa;
static uint8_t hop_idx;
static bool report_bad;

/* 64-bit microsecond clock from the 32-bit TIMER0. */
static uint32_t t_last;
static uint64_t t_high;

static uint64_t widen(uint32_t t32)
{
	/* Reports can arrive slightly out of order with the idle-time clock
	 * reads (a packet queued just before a read is reported after it), so
	 * only a step of more than half the range counts as a wrap; a small step
	 * backwards is an older sample of the same epoch. */
	uint32_t back = t_last - t32;
	uint32_t fwd = t32 - t_last;

	if (t32 < t_last) {
		if (back > 0x80000000u) {               /* wrapped */
			t_high += 1ull << 32;
			t_last = t32;
		}
		return t_high | t32;
	}
	if (fwd > 0x80000000u && t_high) {      /* late sample from before a wrap */
		return (t_high - (1ull << 32)) | t32;
	}
	t_last = t32;
	return t_high | t32;
}

static uint32_t timer_now(void)
{
	NRF_TIMER0->TASKS_CAPTURE[2] = 1;
	return NRF_TIMER0->CC[2];
}

static void radio_isr(const void *arg)
{
	ARG_UNUSED(arg);

	if (!NRF_RADIO->EVENTS_END) {
		return;
	}
	NRF_RADIO->EVENTS_END = 0;

	bool ok = NRF_RADIO->CRCSTATUS != 0;

	if (!ok) {
		rxstats.crc_bad++;
	}
	if (ok || report_bad) {
		struct rx_pkt p;
		uint16_t len = 2u + rxbuf[1];

		p.t_us = NRF_TIMER0->CC[1];
		p.crc = NRF_RADIO->RXCRC & 0xFFFFFF;
		p.ch = cur_ch;
		p.rssi = (uint8_t)NRF_RADIO->RSSISAMPLE;
		p.crc_ok = ok;
		p.pad = 0;
		p.aa = cur_aa;
		memcpy(p.pdu, rxbuf, len);
		if (k_msgq_put(&rxq, &p, K_NO_WAIT) != 0) {
			rxstats.dropped++;
		}
	}
	/* Back to listening: RXIDLE -> RX, the same buffer (already copied). */
	NRF_RADIO->TASKS_START = 1;
}

/* Called with radio_lock held and the radio disabled. */
static void listen_on(uint8_t idx)
{
	uint8_t ch = active.channels[idx];
	uint32_t aa = active.ch_aa[idx] ? active.ch_aa[idx] : active.access_address;
	uint32_t crc = active.ch_aa[idx] ? active.ch_crc[idx] : active.crc_init;

	cur_ch = ch;
	cur_aa = aa;
	radio_set_access_address(aa);
	NRF_RADIO->CRCINIT = crc & 0xFFFFFF;
	radio_set_channel(ch);
	NRF_RADIO->EVENTS_END = 0;
	NRF_RADIO->EVENTS_READY = 0;
	NRF_RADIO->SHORTS = RADIO_SHORTS_READY_START_Msk |
			    RADIO_SHORTS_ADDRESS_RSSISTART_Msk;
	NRF_RADIO->INTENSET = RADIO_INTENSET_END_Msk;
	NRF_RADIO->TASKS_RXEN = 1;
}

void rx_start(void)
{
	adv_stop();                    /* one radio: TX and RX are exclusive */
	rx_stop();

	k_mutex_lock(&cfg_lock, K_FOREVER);
	active = cfg.rx;
	k_mutex_unlock(&cfg_lock);
	if (active.n_channels == 0) {
		active.n_channels = 1;
		active.channels[0] = 37;
		active.ch_aa[0] = 0;
	}
	report_bad = active.report_bad_crc;
	hop_idx = 0;

	k_mutex_lock(&radio_lock, K_FOREVER);
	radio_disable();
	k_msgq_purge(&rxq);
	radio_set_phy(active.phy);
	NRF_RADIO->PACKETPTR = (uint32_t)rxbuf;
	rxstats.running = true;
	listen_on(0);
	k_mutex_unlock(&radio_lock);

	k_sem_give(&rx_wake);
}

void rx_stop(void)
{
	if (!rxstats.running) {
		return;
	}
	k_mutex_lock(&radio_lock, K_FOREVER);
	rxstats.running = false;
	NRF_RADIO->INTENCLR = 0xFFFFFFFFu;
	radio_disable();
	NVIC_ClearPendingIRQ(RADIO_IRQn);
	NRF_RADIO->EVENTS_END = 0;
	k_mutex_unlock(&radio_lock);
}

/* Move to the next channel of the hop list. */
static void hop(void)
{
	k_mutex_lock(&radio_lock, K_FOREVER);
	if (rxstats.running) {
		NRF_RADIO->INTENCLR = RADIO_INTENCLR_END_Msk;
		radio_disable();
		hop_idx = (hop_idx + 1) % active.n_channels;
		listen_on(hop_idx);
		rxstats.hops++;
	}
	k_mutex_unlock(&radio_lock);
}

static void report(const struct rx_pkt *p)
{
	static const char hex[] = "0123456789abcdef";
	/* "aa" is the access address the packet was received on: the receiver
	 * only accepts packets that match it. */
	char s[2 * MAX_RAW + 1];
	uint16_t len = 2u + p->pdu[1];
	uint64_t t = widen(p->t_us);

	for (uint16_t i = 0; i < len; i++) {
		s[2 * i] = hex[p->pdu[i] >> 4];
		s[2 * i + 1] = hex[p->pdu[i] & 0xF];
	}
	s[2 * len] = '\0';

	rxstats.packets++;
	hostif_printf(
		"{\"ev\":\"pkt\",\"n\":%u,\"t\":%u.%06u,\"ch\":%u,\"rssi\":-%u,"
		"\"crc_ok\":%u,\"aa\":\"%08x\",\"pdu\":\"%s\",\"crc\":\"%06x\"}",
		rxstats.packets, (uint32_t)(t / 1000000u), (uint32_t)(t % 1000000u),
		p->ch, p->rssi, p->crc_ok, p->aa, s, p->crc);
}

static void rx_thread(void *a, void *b, void *c)
{
	ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
	int64_t next_hop = 0;

	for (;;) {
		if (!rxstats.running) {
			k_sem_take(&rx_wake, K_FOREVER);
			next_hop = k_uptime_get() + active.dwell_ms;
			continue;
		}

		bool hopping = active.n_channels > 1 && active.dwell_ms > 0;
		int64_t now = k_uptime_get();
		k_timeout_t wait = K_MSEC(200);

		if (hopping) {
			if (now >= next_hop) {
				hop();
				next_hop = now + active.dwell_ms;
			}
			wait = K_MSEC(next_hop - now > 0 ? next_hop - now : 1);
		}

		/* The output queue is the host's back-pressure: hold the packet
		 * here (and let the radio queue absorb, then count, the excess)
		 * rather than have the line dropped after it was formatted. */
		if (hostif_out_free() < 2) {
			k_msleep(1);
			continue;
		}

		struct rx_pkt p;

		if (k_msgq_get(&rxq, &p, wait) == 0) {
			report(&p);
		} else {
			/* Idle: keep the 64-bit clock correct across TIMER0 wraps
			 * (71 minutes) even when nothing is being received. */
			widen(timer_now());
		}
	}
}

K_THREAD_STACK_DEFINE(sniff_stack, 3072);
static struct k_thread sniff_thr;

void rx_init(void)
{
	NRF_TIMER0->TASKS_STOP = 1;
	NRF_TIMER0->MODE = TIMER_MODE_MODE_Timer;
	NRF_TIMER0->BITMODE = TIMER_BITMODE_BITMODE_32Bit;
	NRF_TIMER0->PRESCALER = 4;                 /* 16 MHz / 2^4 = 1 MHz */
	NRF_TIMER0->TASKS_CLEAR = 1;
	NRF_TIMER0->TASKS_START = 1;
	NRF_PPI->CHENSET = 1u << PPI_CH_ADDRESS_TO_TIMER0;

	IRQ_CONNECT(RADIO_IRQn, 1, radio_isr, NULL, 0);
	irq_enable(RADIO_IRQn);

	/* Lower priority than the USB writer (9): it must drain what this
	 * thread produces, or the output queue would only ever fill. */
	k_thread_create(&sniff_thr, sniff_stack, K_THREAD_STACK_SIZEOF(sniff_stack),
			rx_thread, NULL, NULL, NULL, 11, 0, K_NO_WAIT);
	k_thread_name_set(&sniff_thr, "sniff");
}
