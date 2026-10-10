/*
 * ble_trx -- nRF52840 BLE packet generator (TX) and sniffer (RX).
 *
 * Shared configuration and the interfaces between the five parts:
 *   hostif.c  USB CDC line I/O, DFU entry
 *   cmd.c     the command language
 *   adv.c     the RADIO set-up and the advertising (TX) scheduler
 *   rx.c      the receiver (sniffer) and its packet reporter
 *   store.c   saving the configuration to flash
 */
#ifndef SIM_H_
#define SIM_H_

#include <stdbool.h>
#include <stdint.h>
#include <zephyr/kernel.h>

#define ADV_ACCESS_ADDRESS 0x8E89BED6u
#define ADV_CRC_INIT       0x555555u
#define MAX_ADV_DATA       31
#define MAX_CHANNELS       8
/* A whole PDU as it goes on air after the access address: 2-byte header plus
 * up to 255 payload bytes (LENGTH is an 8-bit field, PCNF1.MAXLEN = 255). */
#define MAX_RAW            257

enum autostart {
	AUTOSTART_OFF = 0,
	AUTOSTART_TX = 1,
	AUTOSTART_RX = 2,
};

/* Receiver configuration (rx.c).  Independent of the TX settings so a board
 * can be switched between transmitter and sniffer without re-entering them. */
struct rx_cfg {
	uint8_t channels[MAX_CHANNELS];  /* one channel, or a hop list */
	uint8_t n_channels;
	/* Per-channel access address / CRC init; 0 = use the ones below.  Lets
	 * one hop list mix advertising channels and a connection's channel. */
	uint32_t ch_aa[MAX_CHANNELS];
	uint32_t ch_crc[MAX_CHANNELS];
	uint8_t phy;                     /* enum phy */
	uint32_t access_address;
	uint32_t crc_init;
	uint16_t dwell_ms;               /* per channel when hopping */
	bool report_bad_crc;             /* also report CRC-failed packets */
};

enum phy {
	PHY_BLE1M = 0,  /* BLE 1M: +-250 kHz, h = 0.5 */
	PHY_NRF1M = 1,  /* Nordic proprietary 1M: +-170 kHz, h ~ 0.34 */
};

/* One transmitter "personality".  The twin is a second one interleaved with
 * the first, so a single board can stand in for an impersonator sharing the
 * legitimate device's address but not its radio characteristics. */
struct persona {
	uint8_t addr[6];        /* on-air order: addr[0] is the LSB octet */
	bool addr_random;
	int8_t power_dbm;
	uint8_t phy;            /* enum phy */
};

struct sim_cfg {
	uint32_t magic;
	uint16_t version;
	uint16_t size;

	struct persona main;
	struct persona twin;
	bool twin_on;
	bool twin_same_addr;    /* twin uses main's address (impersonation) */
	uint8_t twin_pct;       /* share of events sent by the twin, 0-100 */

	uint8_t pdu_type;       /* 0 ADV_IND, 1 DIRECT, 2 NONCONN, 6 SCAN_IND */
	uint8_t target[6];      /* TargetA for ADV_DIRECT_IND */
	bool target_random;

	uint8_t adv_data[MAX_ADV_DATA];
	uint8_t adv_len;
	bool counter_on;        /* append a 16-bit event counter (AD 0xFF, 0xFFFF) */

	uint8_t channels[MAX_CHANNELS];
	uint8_t n_channels;
	uint32_t access_address;
	uint32_t crc_init;

	uint16_t interval_ms;
	bool adv_delay;         /* spec advDelay: random 0-10 ms per event */
	uint8_t crc_err_pct;    /* share of packets sent with a corrupt CRC */
	uint16_t gap_us;        /* between the channels of one event */

	uint8_t autostart;      /* enum autostart: TX or RX at power-up */

	/* Raw mode: transmit this PDU (header + payload) verbatim instead of
	 * the one built from addr/pdu/data.  CRC and whitening still applied. */
	bool raw_on;
	uint16_t raw_len;
	uint8_t raw[MAX_RAW];

	struct rx_cfg rx;
	uint32_t crc32;
};

#define SIM_MAGIC   0x53494D31u /* "SIM1" */
#define SIM_VERSION 3   /* 2: raw PDU, receiver, autostart; 3: per-channel RX AA */

extern struct sim_cfg cfg;
extern struct k_mutex cfg_lock;

struct sim_stats {
	uint32_t events;
	uint32_t packets;
	uint32_t twin_events;
	uint32_t crc_errors;
	uint32_t remaining;     /* events left in a "send N"; 0 = unlimited */
	uint16_t counter;
	bool running;
};

extern volatile struct sim_stats stats;

struct rx_stats {
	uint32_t packets;       /* reported to the host */
	uint32_t crc_bad;       /* received with a CRC failure (reported or not) */
	uint32_t dropped;       /* lost because the report queue was full */
	uint32_t hops;
	bool running;
};

extern volatile struct rx_stats rxstats;

/* Owned by whichever of TX and RX is using the radio right now. */
extern struct k_mutex radio_lock;

/* hostif.c */
void hostif_init(void (*on_line)(char *line));
void hostif_printf(const char *fmt, ...);
void hostif_reboot_dfu(void);
uint32_t hostif_out_free(void);
uint32_t hostif_dropped(void);

/* adv.c */
void adv_init(void);
void adv_start(uint32_t count);
void adv_stop(void);
const char *phy_name(uint8_t phy);
int adv_power_supported(int dbm);
uint32_t channel_mhz(uint8_t ch);
void radio_set_phy(uint8_t phy);
void radio_set_channel(uint8_t ch);
void radio_set_access_address(uint32_t aa);
void radio_disable(void);

/* rx.c */
void rx_init(void);
void rx_start(void);
void rx_stop(void);

/* cmd.c */
void cmd_line(char *line);
void cfg_defaults(struct sim_cfg *c);

/* store.c */
int store_save(const struct sim_cfg *c);
int store_load(struct sim_cfg *c);
int store_erase(void);

#endif
