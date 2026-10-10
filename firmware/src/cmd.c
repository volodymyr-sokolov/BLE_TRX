/*
 * The command language.  One line per command, JSON acknowledgements out.
 *
 * Every on-air parameter is a command so the host-side test scripts can drive
 * a whole experiment over the wire.  `help` lists them; `info` dumps the
 * configuration the sniffer should expect to see.
 */
#include "sim.h"

#include <ctype.h>
#include <stdlib.h>
#include <string.h>
#include <zephyr/sys/reboot.h>

struct sim_cfg cfg;
K_MUTEX_DEFINE(cfg_lock);

void cfg_defaults(struct sim_cfg *c)
{
	memset(c, 0, sizeof(*c));
	c->magic = SIM_MAGIC;
	c->version = SIM_VERSION;
	c->size = sizeof(*c);

	/* A recognisable default identity and a Flags + Complete Local Name. */
	static const uint8_t addr[6] = {0x34, 0x12, 0xEF, 0xBE, 0xAD, 0xDE};
	memcpy(c->main.addr, addr, 6);
	c->main.addr_random = true;
	c->main.power_dbm = 0;
	c->main.phy = PHY_BLE1M;

	static const uint8_t twin[6] = {0x02, 0x00, 0x00, 0x00, 0x00, 0xC0};
	memcpy(c->twin.addr, twin, 6);
	c->twin.addr_random = true;
	c->twin.power_dbm = 0;
	c->twin.phy = PHY_NRF1M;      /* a different modulation index by default */
	c->twin_pct = 50;

	c->pdu_type = 0x00;           /* ADV_IND */
	static const uint8_t ad[] = {
		0x02, 0x01, 0x06,                               /* Flags */
		0x07, 0x09, 'b', 'l', 'e', 's', 'i', 'm',       /* Complete Local Name */
	};
	memcpy(c->adv_data, ad, sizeof(ad));
	c->adv_len = sizeof(ad);
	c->counter_on = true;

	c->channels[0] = 37;
	c->channels[1] = 38;
	c->channels[2] = 39;
	c->n_channels = 3;
	c->access_address = ADV_ACCESS_ADDRESS;
	c->crc_init = ADV_CRC_INIT;

	c->interval_ms = 100;
	c->adv_delay = true;
	c->gap_us = 1500;
	c->crc_err_pct = 0;
	c->autostart = AUTOSTART_OFF;

	c->raw_on = false;
	c->raw_len = 0;

	/* Receiver: advertising channel 37, good-CRC packets only. */
	c->rx.channels[0] = 37;
	c->rx.n_channels = 1;
	c->rx.phy = PHY_BLE1M;
	c->rx.access_address = ADV_ACCESS_ADDRESS;
	c->rx.crc_init = ADV_CRC_INIT;
	c->rx.dwell_ms = 0;
	c->rx.report_bad_crc = false;
}

/* ---- parsing helpers --------------------------------------------------- */

static char *next(char **p)
{
	char *s = *p;

	while (*s == ' ' || *s == '\t') {
		s++;
	}
	if (!*s) {
		*p = s;
		return NULL;
	}
	char *start = s;

	while (*s && *s != ' ' && *s != '\t') {
		s++;
	}
	if (*s) {
		*s++ = '\0';
	}
	*p = s;
	return start;
}

static bool parse_mac(const char *s, uint8_t out[6])
{
	/* Human order AA:BB:CC:DD:EE:FF -> on-air LSB-first out[0]=FF. */
	unsigned v[6];

	if (sscanf(s, "%x:%x:%x:%x:%x:%x", &v[0], &v[1], &v[2], &v[3], &v[4], &v[5]) != 6) {
		return false;
	}
	for (int i = 0; i < 6; i++) {
		if (v[i] > 0xFF) {
			return false;
		}
		out[i] = (uint8_t)v[5 - i];
	}
	return true;
}

static int hexval(char c)
{
	if (c >= '0' && c <= '9') {
		return c - '0';
	}
	c = (char)tolower((unsigned char)c);
	return (c >= 'a' && c <= 'f') ? c - 'a' + 10 : -1;
}

/* Hex bytes from the rest of a command line.  Whitespace (and ':' '-' ','
 * separators) anywhere is ignored, so "80 60 64 1c ae 7a", "80:60:64..." and
 * "8060641cae7a" are the same six bytes.  -1 on an odd digit count, a
 * non-hex character, or more than `max` bytes. */
static int parse_hex_bytes(const char *s, uint8_t *out, int max)
{
	int n = 0;
	int hi = -1;

	for (; *s; s++) {
		if (*s == ' ' || *s == '\t' || *s == ':' || *s == '-' || *s == ',') {
			continue;
		}
		int v = hexval(*s);

		if (v < 0) {
			return -1;
		}
		if (hi < 0) {
			hi = v;
			continue;
		}
		if (n >= max) {
			return -1;
		}
		out[n++] = (uint8_t)((hi << 4) | v);
		hi = -1;
	}
	return hi < 0 ? n : -1;
}

/* Everything left on the line (leading blanks skipped), or NULL. */
static char *rest(char **p)
{
	char *s = *p;

	while (*s == ' ' || *s == '\t') {
		s++;
	}
	*p = s + strlen(s);
	return *s ? s : NULL;
}

static const char *autostart_name(uint8_t a)
{
	return a == AUTOSTART_TX ? "on" : a == AUTOSTART_RX ? "rx" : "off";
}

static bool parse_channel_list(char **p, uint8_t *ch, uint8_t *count)
{
	uint8_t n = 0;
	char *v;

	while ((v = next(p))) {
		char *end;
		long idx = strtol(v, &end, 10);

		if (*end || idx < 0 || idx > 39 || n >= MAX_CHANNELS) {
			return false;
		}
		ch[n++] = (uint8_t)idx;
	}
	*count = n;
	return n > 0;
}

static void emit_mac(char *dst, const uint8_t a[6])
{
	snprintk(dst, 18, "%02X:%02X:%02X:%02X:%02X:%02X",
		 a[5], a[4], a[3], a[2], a[1], a[0]);
}

static void ack(const char *key, const char *val)
{
	hostif_printf("{\"ok\":\"%s\",\"%s\":\"%s\"}", key, key, val ? val : "");
}

static void err(const char *msg)
{
	hostif_printf("{\"err\":\"%s\"}", msg);
}

/* ---- info / help ------------------------------------------------------- */

static void hex_of(char *dst, const uint8_t *b, size_t n)
{
	static const char hex[] = "0123456789abcdef";

	for (size_t i = 0; i < n; i++) {
		dst[2 * i] = hex[b[i] >> 4];
		dst[2 * i + 1] = hex[b[i] & 0xF];
	}
	dst[2 * n] = '\0';
}

static void print_raw(void)
{
	char h[2 * MAX_RAW + 1];

	hex_of(h, cfg.raw, cfg.raw_len);
	hostif_printf("{\"ev\":\"raw\",\"on\":%d,\"len\":%u,\"pdu\":\"%s\"}",
		      cfg.raw_on, cfg.raw_len, h);
}

static void print_rx(void)
{
	char ch[MAX_CHANNELS * 26 + 1];
	size_t o = 0;

	for (uint8_t i = 0; i < cfg.rx.n_channels; i++) {
		if (cfg.rx.ch_aa[i]) {
			o += snprintk(&ch[o], sizeof(ch) - o, "%s\"%u:%08x:%06x\"",
				      i ? "," : "", cfg.rx.channels[i],
				      cfg.rx.ch_aa[i], cfg.rx.ch_crc[i]);
		} else {
			o += snprintk(&ch[o], sizeof(ch) - o, "%s%u", i ? "," : "",
				      cfg.rx.channels[i]);
		}
	}
	ch[o] = '\0';
	hostif_printf(
		"{\"ev\":\"rx\",\"running\":%d,\"channels\":[%s],\"phy\":\"%s\","
		"\"aa\":\"%08x\",\"crc_init\":\"%06x\",\"dwell_ms\":%u,\"bad_crc\":%d,"
		"\"packets\":%u,\"crc_bad\":%u,\"dropped\":%u,\"hops\":%u}",
		rxstats.running, ch, phy_name(cfg.rx.phy), cfg.rx.access_address,
		cfg.rx.crc_init, cfg.rx.dwell_ms, cfg.rx.report_bad_crc,
		rxstats.packets, rxstats.crc_bad, rxstats.dropped, rxstats.hops);
}

static void print_info(void)
{
	char a[18], t[18], w[18];

	k_mutex_lock(&cfg_lock, K_FOREVER);
	emit_mac(a, cfg.main.addr);
	emit_mac(t, cfg.twin.addr);
	emit_mac(w, cfg.target);
	hostif_printf(
		"{\"ev\":\"info\",\"running\":%d,\"addr\":\"%s\",\"random\":%d,"
		"\"phy\":\"%s\",\"power\":%d,\"pdu\":%u,\"adlen\":%u,\"counter\":%d}",
		stats.running, a, cfg.main.addr_random, phy_name(cfg.main.phy),
		cfg.main.power_dbm, cfg.pdu_type, cfg.adv_len, cfg.counter_on);
	hostif_printf(
		"{\"ev\":\"twin\",\"on\":%d,\"same_addr\":%d,\"pct\":%u,\"addr\":\"%s\","
		"\"phy\":\"%s\",\"power\":%d}",
		cfg.twin_on, cfg.twin_same_addr, cfg.twin_pct, t,
		phy_name(cfg.twin.phy), cfg.twin.power_dbm);
	hostif_printf(
		"{\"ev\":\"chan\",\"n\":%u,\"first\":%u,\"aa\":%u,\"crc_init\":%u,"
		"\"interval_ms\":%u,\"gap_us\":%u,\"crc_err_pct\":%u,\"target\":\"%s\"}",
		cfg.n_channels, cfg.n_channels ? cfg.channels[0] : 0,
		cfg.access_address, cfg.crc_init, cfg.interval_ms, cfg.gap_us,
		cfg.crc_err_pct, w);
	hostif_printf(
		"{\"ev\":\"stats\",\"events\":%u,\"packets\":%u,\"twin_events\":%u,"
		"\"crc_errors\":%u}",
		stats.events, stats.packets, stats.twin_events, stats.crc_errors);
	print_raw();
	print_rx();
	hostif_printf("{\"ev\":\"mode\",\"tx\":%d,\"rx\":%d,\"boot\":\"%s\","
		      "\"adv_delay\":%d,\"fw\":\"ble_trx\",\"ver\":%u,"
		      "\"out_dropped\":%u}",
		      stats.running, rxstats.running, autostart_name(cfg.autostart),
		      cfg.adv_delay, SIM_VERSION, hostif_dropped());
	k_mutex_unlock(&cfg_lock);
}

static void print_help(void)
{
	static const char *const lines[] = {
		"commands (one per line; values are decimal unless 0x-prefixed):",
		"  start [N]            advertise forever, or N events then stop",
		"  stop",
		"  addr AA:BB:..:FF     main advertiser address (human order)",
		"  type public|random   main address type",
		"  pdu adv_ind|adv_direct_ind|adv_nonconn_ind|adv_scan_ind",
		"  power <dBm>          8,7,6,5,4,3,2,0,-4,-8,-12,-16,-20,-40",
		"  phy ble1m|nrf1m      modulation (nrf1m has a lower mod index)",
		"  channels 37 38 39    advertising and/or data channel indices",
		"  aa 0xNNNNNNNN        access address (data channels)",
		"  crcinit 0xNNNNNN     CRC init / whitening-independent seed",
		"  interval <ms>        between advertising events",
		"  gap <us>             between channels within one event",
		"  data <hex>           raw AD structures, e.g. 020106... (spaces ok)",
		"  name <text>          shortcut: set a Complete Local Name AD",
		"  counter on|off       append a 16-bit event counter AD",
		"  crcerr <pct>         send this %% of packets with a bad CRC",
		"  twin on|off          enable the second transmitter personality",
		"  twin addr AA:..|same set the twin's address, or reuse main's",
		"  twin phy ble1m|nrf1m / twin power <dBm> / twin pct <0-100>",
		"  direct AA:BB:..:FF   TargetA for adv_direct_ind",
		"  advdelay on|off      random 0-10 ms advDelay added to each interval",
		"  raw <hex>            send this PDU (header+payload) verbatim; spaces ok",
		"  raw on|off           switch between the raw PDU and the built one",
		"  raw                  show the raw PDU",
		"rx (sniffer; starting RX stops TX and vice versa):",
		"  rx on|off            start / stop receiving (also: rx start|stop)",
		"  rx ch 37 [38 39]     channel, or a hop list (with rx dwell);",
		"                       ch:aa[:crcinit] gives one channel its own AA",
		"  rx dwell <ms>        time per channel when hopping (0 = no hop)",
		"  rx aa 0xNNNNNNNN     access address to receive on",
		"  rx crcinit 0xNNNNNN  CRC init to check against",
		"  rx phy ble1m|nrf1m   receiver modulation",
		"  rx crc ok|all        report good-CRC packets only, or all",
		"  rx adv               shortcut: aa 0x8E89BED6, crcinit 0x555555",
		"  rx info              receiver settings and counters",
		"  defaults             reset the whole configuration (not saved)",
		"  save | load | erase  persist / restore / clear the configuration",
		"  boot on|off|rx       at power-up: advertise / nothing / receive",
		"  info | help | dfu | reboot",
	};
	for (size_t i = 0; i < ARRAY_SIZE(lines); i++) {
		hostif_printf("%s", lines[i]);
	}
}

/* ---- the big switch ---------------------------------------------------- */

static bool set_phy_word(const char *v, uint8_t *out)
{
	if (!strcmp(v, "ble1m")) {
		*out = PHY_BLE1M;
	} else if (!strcmp(v, "nrf1m")) {
		*out = PHY_NRF1M;
	} else {
		return false;
	}
	return true;
}

/* "37 38 5:0x9E8B4B27:0x123456": channel[:aa[:crcinit]] tokens. */
static bool parse_rx_channels(char **p, struct rx_cfg *rx)
{
	uint8_t n = 0;
	char *v;

	while ((v = next(p))) {
		char *end;
		long idx = strtol(v, &end, 10);
		uint32_t aa = 0, crc = ADV_CRC_INIT;

		if (idx < 0 || idx > 39 || n >= MAX_CHANNELS) {
			return false;
		}
		if (*end == ':') {
			aa = (uint32_t)strtoul(end + 1, &end, 0);
			if (aa == 0) {
				return false;
			}
			if (*end == ':') {
				crc = (uint32_t)strtoul(end + 1, &end, 0) & 0xFFFFFF;
			}
		}
		if (*end) {
			return false;
		}
		rx->channels[n] = (uint8_t)idx;
		rx->ch_aa[n] = aa;
		rx->ch_crc[n] = crc;
		n++;
	}
	rx->n_channels = n;
	return n > 0;
}

static void cmd_raw(char **p)
{
	char *v = rest(p);
	uint8_t buf[MAX_RAW];

	if (!v) {
		k_mutex_lock(&cfg_lock, K_FOREVER);
		print_raw();
		k_mutex_unlock(&cfg_lock);
		return;
	}
	if (!strcmp(v, "on") || !strcmp(v, "off")) {
		if (!strcmp(v, "on") && cfg.raw_len < 2) {
			err("no raw PDU set; raw <hex>");
			return;
		}
		cfg.raw_on = !strcmp(v, "on");
		ack("raw", v);
		return;
	}

	int n = parse_hex_bytes(v, buf, MAX_RAW);

	if (n < 2) {
		err("raw <hex PDU: header(2) + payload, 2-257 bytes>");
		return;
	}
	k_mutex_lock(&cfg_lock, K_FOREVER);
	memset(cfg.raw, 0, sizeof(cfg.raw));
	memcpy(cfg.raw, buf, n);
	cfg.raw_len = (uint16_t)n;
	cfg.raw_on = true;
	k_mutex_unlock(&cfg_lock);
	/* The radio sends LENGTH (pdu[1]) payload bytes whatever was supplied;
	 * report both so a host can see a mismatch. */
	hostif_printf("{\"ok\":\"raw\",\"raw\":\"set\",\"len\":%d,"
		      "\"length_field\":%u,\"on_air\":%u}",
		      n, buf[1], 2u + buf[1]);
}

static void cmd_rx(char **p)
{
	char *sub = next(p);
	bool retune = true;

	if (!sub) {
		err("rx on|off|ch|dwell|aa|crcinit|phy|crc|adv|info");
		return;
	}
	if (!strcmp(sub, "on") || !strcmp(sub, "start")) {
		rx_start();
		ack("rx", "on");
		retune = false;
	} else if (!strcmp(sub, "off") || !strcmp(sub, "stop")) {
		rx_stop();
		ack("rx", "off");
		retune = false;
	} else if (!strcmp(sub, "info")) {
		k_mutex_lock(&cfg_lock, K_FOREVER);
		print_rx();
		k_mutex_unlock(&cfg_lock);
		retune = false;
	} else if (!strcmp(sub, "ch") || !strcmp(sub, "channels")) {
		struct rx_cfg tmp = cfg.rx;

		if (!parse_rx_channels(p, &tmp)) {
			err("rx ch: 1-8 of ch[:aa[:crcinit]], e.g. rx ch 37 5:0x9E8B4B27:0x123456");
			return;
		}
		k_mutex_lock(&cfg_lock, K_FOREVER);
		cfg.rx = tmp;
		k_mutex_unlock(&cfg_lock);
		ack("rx_ch", "set");
	} else if (!strcmp(sub, "dwell")) {
		char *v = next(p);

		if (!v) {
			err("rx dwell <ms>");
			return;
		}
		cfg.rx.dwell_ms = (uint16_t)strtoul(v, NULL, 0);
		ack("rx_dwell", v);
	} else if (!strcmp(sub, "aa")) {
		char *v = next(p);

		if (!v) {
			err("rx aa 0xNNNNNNNN");
			return;
		}
		cfg.rx.access_address = (uint32_t)strtoul(v, NULL, 0);
		ack("rx_aa", v);
	} else if (!strcmp(sub, "crcinit")) {
		char *v = next(p);

		if (!v) {
			err("rx crcinit 0xNNNNNN");
			return;
		}
		cfg.rx.crc_init = (uint32_t)strtoul(v, NULL, 0) & 0xFFFFFF;
		ack("rx_crcinit", v);
	} else if (!strcmp(sub, "phy")) {
		char *v = next(p);

		if (!v || !set_phy_word(v, &cfg.rx.phy)) {
			err("rx phy ble1m|nrf1m");
			return;
		}
		ack("rx_phy", v);
	} else if (!strcmp(sub, "crc")) {
		char *v = next(p);

		if (!v || (strcmp(v, "ok") && strcmp(v, "all"))) {
			err("rx crc ok|all");
			return;
		}
		cfg.rx.report_bad_crc = !strcmp(v, "all");
		ack("rx_crc", v);
	} else if (!strcmp(sub, "adv")) {
		cfg.rx.access_address = ADV_ACCESS_ADDRESS;
		cfg.rx.crc_init = ADV_CRC_INIT;
		ack("rx_adv", "ok");
	} else {
		err("unknown rx subcommand");
		return;
	}
	/* A setting changed while receiving takes effect immediately. */
	if (retune && rxstats.running) {
		rx_start();
	}
}

void cmd_line(char *line)
{
	char *p = line;
	char *c = next(&p);

	if (!c) {
		return;
	}

	if (!strcmp(c, "start")) {
		char *n = next(&p);

		adv_start(n ? (uint32_t)strtoul(n, NULL, 0) : 0);
		ack("start", n ? n : "0");
	} else if (!strcmp(c, "stop")) {
		adv_stop();
		ack("stop", "");
	} else if (!strcmp(c, "info")) {
		print_info();
	} else if (!strcmp(c, "help")) {
		print_help();
	} else if (!strcmp(c, "dfu")) {
		hostif_printf("{\"ev\":\"dfu\"}");
		hostif_reboot_dfu();
	} else if (!strcmp(c, "reboot")) {
		hostif_printf("{\"ev\":\"reboot\"}");
		k_msleep(50);
		sys_reboot(SYS_REBOOT_COLD);
	} else if (!strcmp(c, "addr")) {
		char *v = next(&p);
		uint8_t m[6];

		if (v && parse_mac(v, m)) {
			k_mutex_lock(&cfg_lock, K_FOREVER);
			memcpy(cfg.main.addr, m, 6);
			k_mutex_unlock(&cfg_lock);
			ack("addr", v);
		} else {
			err("addr AA:BB:CC:DD:EE:FF");
		}
	} else if (!strcmp(c, "type")) {
		char *v = next(&p);

		if (v && (!strcmp(v, "public") || !strcmp(v, "random"))) {
			cfg.main.addr_random = !strcmp(v, "random");
			ack("type", v);
		} else {
			err("type public|random");
		}
	} else if (!strcmp(c, "pdu")) {
		char *v = next(&p);

		if (!v) {
			err("pdu <type>");
		} else if (!strcmp(v, "adv_ind")) {
			cfg.pdu_type = 0x00; ack("pdu", v);
		} else if (!strcmp(v, "adv_direct_ind")) {
			cfg.pdu_type = 0x01; ack("pdu", v);
		} else if (!strcmp(v, "adv_nonconn_ind")) {
			cfg.pdu_type = 0x02; ack("pdu", v);
		} else if (!strcmp(v, "adv_scan_ind")) {
			cfg.pdu_type = 0x06; ack("pdu", v);
		} else {
			err("unknown pdu type");
		}
	} else if (!strcmp(c, "power")) {
		char *v = next(&p);
		int dbm = v ? atoi(v) : 99;

		if (v && adv_power_supported(dbm)) {
			cfg.main.power_dbm = (int8_t)dbm;
			ack("power", v);
		} else {
			err("unsupported power; see help");
		}
	} else if (!strcmp(c, "phy")) {
		char *v = next(&p);

		if (v && set_phy_word(v, (uint8_t *)&cfg.main.phy)) {
			ack("phy", v);
		} else {
			err("phy ble1m|nrf1m");
		}
	} else if (!strcmp(c, "channels")) {
		uint8_t ch[MAX_CHANNELS];
		uint8_t n = 0;

		if (!parse_channel_list(&p, ch, &n)) {
			err("channels: 1-8 indices 0-39, e.g. channels 37 38 39");
		} else {
			k_mutex_lock(&cfg_lock, K_FOREVER);
			memcpy(cfg.channels, ch, n);
			cfg.n_channels = n;
			k_mutex_unlock(&cfg_lock);
			ack("channels", "set");
		}
	} else if (!strcmp(c, "aa")) {
		char *v = next(&p);

		if (v) {
			cfg.access_address = (uint32_t)strtoul(v, NULL, 0);
			ack("aa", v);
		} else {
			err("aa 0xNNNNNNNN");
		}
	} else if (!strcmp(c, "crcinit")) {
		char *v = next(&p);

		if (v) {
			cfg.crc_init = (uint32_t)strtoul(v, NULL, 0) & 0xFFFFFF;
			ack("crcinit", v);
		} else {
			err("crcinit 0xNNNNNN");
		}
	} else if (!strcmp(c, "interval")) {
		char *v = next(&p);

		if (v) {
			cfg.interval_ms = (uint16_t)strtoul(v, NULL, 0);
			ack("interval", v);
		} else {
			err("interval <ms>");
		}
	} else if (!strcmp(c, "gap")) {
		char *v = next(&p);

		if (v) {
			cfg.gap_us = (uint16_t)strtoul(v, NULL, 0);
			ack("gap", v);
		} else {
			err("gap <us>");
		}
	} else if (!strcmp(c, "data")) {
		char *v = rest(&p);
		uint8_t buf[MAX_ADV_DATA];
		int n = v ? parse_hex_bytes(v, buf, MAX_ADV_DATA) : -1;

		if (n >= 0) {
			k_mutex_lock(&cfg_lock, K_FOREVER);
			memcpy(cfg.adv_data, buf, n);
			cfg.adv_len = (uint8_t)n;
			k_mutex_unlock(&cfg_lock);
			ack("data", "set");
		} else {
			err("data <even-length hex, <=31 bytes>");
		}
	} else if (!strcmp(c, "name")) {
		char *v = next(&p);
		size_t ln = v ? strlen(v) : 0;

		if (v && ln <= MAX_ADV_DATA - 5) {
			k_mutex_lock(&cfg_lock, K_FOREVER);
			cfg.adv_data[0] = 0x02;
			cfg.adv_data[1] = 0x01;
			cfg.adv_data[2] = 0x06;
			cfg.adv_data[3] = (uint8_t)(ln + 1);
			cfg.adv_data[4] = 0x09;
			memcpy(&cfg.adv_data[5], v, ln);
			cfg.adv_len = (uint8_t)(5 + ln);
			k_mutex_unlock(&cfg_lock);
			ack("name", v);
		} else {
			err("name too long");
		}
	} else if (!strcmp(c, "counter")) {
		char *v = next(&p);

		cfg.counter_on = v && !strcmp(v, "on");
		ack("counter", cfg.counter_on ? "on" : "off");
	} else if (!strcmp(c, "crcerr")) {
		char *v = next(&p);
		int pct = v ? atoi(v) : -1;

		if (pct >= 0 && pct <= 100) {
			cfg.crc_err_pct = (uint8_t)pct;
			ack("crcerr", v);
		} else {
			err("crcerr 0-100");
		}
	} else if (!strcmp(c, "direct")) {
		char *v = next(&p);
		uint8_t m[6];

		if (v && parse_mac(v, m)) {
			memcpy(cfg.target, m, 6);
			ack("direct", v);
		} else {
			err("direct AA:BB:CC:DD:EE:FF");
		}
	} else if (!strcmp(c, "twin")) {
		char *sub = next(&p);

		if (!sub) {
			err("twin on|off|addr|same|phy|power|pct");
		} else if (!strcmp(sub, "on")) {
			cfg.twin_on = true; ack("twin", "on");
		} else if (!strcmp(sub, "off")) {
			cfg.twin_on = false; ack("twin", "off");
		} else if (!strcmp(sub, "same")) {
			cfg.twin_same_addr = true; ack("twin", "same addr");
		} else if (!strcmp(sub, "addr")) {
			char *v = next(&p);
			uint8_t m[6];

			if (v && parse_mac(v, m)) {
				memcpy(cfg.twin.addr, m, 6);
				cfg.twin_same_addr = false;
				ack("twin_addr", v);
			} else {
				err("twin addr AA:BB:CC:DD:EE:FF");
			}
		} else if (!strcmp(sub, "phy")) {
			char *v = next(&p);

			if (v && set_phy_word(v, (uint8_t *)&cfg.twin.phy)) {
				ack("twin_phy", v);
			} else {
				err("twin phy ble1m|nrf1m");
			}
		} else if (!strcmp(sub, "power")) {
			char *v = next(&p);
			int dbm = v ? atoi(v) : 99;

			if (v && adv_power_supported(dbm)) {
				cfg.twin.power_dbm = (int8_t)dbm;
				ack("twin_power", v);
			} else {
				err("unsupported power; see help");
			}
		} else if (!strcmp(sub, "pct")) {
			char *v = next(&p);
			int pct = v ? atoi(v) : -1;

			if (pct >= 0 && pct <= 100) {
				cfg.twin_pct = (uint8_t)pct;
				ack("twin_pct", v);
			} else {
				err("twin pct 0-100");
			}
		} else {
			err("unknown twin subcommand");
		}
	} else if (!strcmp(c, "save")) {
		int rc = store_save(&cfg);

		if (rc == 0) {
			ack("save", "ok");
		} else {
			err("save failed");
		}
	} else if (!strcmp(c, "load")) {
		struct sim_cfg tmp;

		if (store_load(&tmp) == 0) {
			k_mutex_lock(&cfg_lock, K_FOREVER);
			cfg = tmp;
			k_mutex_unlock(&cfg_lock);
			ack("load", "ok");
		} else {
			err("no saved configuration");
		}
	} else if (!strcmp(c, "erase")) {
		store_erase();
		ack("erase", "ok");
	} else if (!strcmp(c, "boot")) {
		char *v = next(&p);

		if (v && !strcmp(v, "on")) {
			cfg.autostart = AUTOSTART_TX;
		} else if (v && !strcmp(v, "rx")) {
			cfg.autostart = AUTOSTART_RX;
		} else if (v && !strcmp(v, "off")) {
			cfg.autostart = AUTOSTART_OFF;
		} else {
			err("boot on|off|rx");
			return;
		}
		ack("boot", autostart_name(cfg.autostart));
	} else if (!strcmp(c, "advdelay")) {
		char *v = next(&p);

		if (v && (!strcmp(v, "on") || !strcmp(v, "off"))) {
			cfg.adv_delay = !strcmp(v, "on");
			ack("advdelay", v);
		} else {
			err("advdelay on|off");
		}
	} else if (!strcmp(c, "defaults")) {
		adv_stop();
		rx_stop();
		k_mutex_lock(&cfg_lock, K_FOREVER);
		cfg_defaults(&cfg);
		k_mutex_unlock(&cfg_lock);
		ack("defaults", "ok");
	} else if (!strcmp(c, "raw")) {
		cmd_raw(&p);
	} else if (!strcmp(c, "rx")) {
		cmd_rx(&p);
	} else {
		err("unknown command; type help");
	}
}
