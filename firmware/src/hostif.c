/*
 * USB CDC line interface: queued output, line-assembled input, and DFU entry.
 *
 * Output goes through a queue drained by its own thread so that a host that is
 * not reading -- or not even connected -- can never stall the advertising
 * scheduler: a full queue drops the line and counts it instead of blocking.
 */
#include "sim.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/sys/reboot.h>
#include <hal/nrf_power.h>

/* A received 257-byte PDU is 514 hex characters plus ~130 of JSON around it. */
#define LINE_MAX_OUT 704
/* `raw` with a 257-byte PDU, even typed with a space between every byte. */
#define LINE_MAX_IN 800

struct out_line {
	char s[LINE_MAX_OUT];
};

K_MSGQ_DEFINE(outq, sizeof(struct out_line), 32, 4);

static const struct device *uart;
static void (*line_cb)(char *line);
static atomic_t dropped;

/*
 * Reboot into the Adafruit nRF52 bootloader's serial DFU mode.  These boards
 * have no SWD probe and no mass-storage volume; GPREGRET magic (or a physical
 * double tap of RESET) is the only way back into the bootloader, so the
 * application has to offer it or the board can only be reflashed by hand.
 */
#define DFU_MAGIC_SERIAL_ONLY_RESET 0x4Eu

void hostif_reboot_dfu(void)
{
	k_busy_wait(30000); /* let the last line reach the host */
	nrf_power_gpregret_set(NRF_POWER, 0, DFU_MAGIC_SERIAL_ONLY_RESET);
	NVIC_SystemReset();
}

void hostif_printf(const char *fmt, ...)
{
	struct out_line l;
	va_list ap;

	va_start(ap, fmt);
	int n = vsnprintk(l.s, sizeof(l.s) - 2, fmt, ap);
	va_end(ap);
	if (n < 0) {
		return;
	}
	if (n > (int)sizeof(l.s) - 3) {
		n = sizeof(l.s) - 3;
	}
	l.s[n] = '\r';
	l.s[n + 1] = '\n';
	if (n + 2 < (int)sizeof(l.s)) {
		l.s[n + 2] = '\0';
	}
	if (k_msgq_put(&outq, &l, K_NO_WAIT) != 0) {
		atomic_inc(&dropped);
	}
}

uint32_t hostif_out_free(void)
{
	return k_msgq_num_free_get(&outq);
}

uint32_t hostif_dropped(void)
{
	return (uint32_t)atomic_get(&dropped);
}

static void tx_thread(void *a, void *b, void *c)
{
	struct out_line l;

	ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
	for (;;) {
		k_msgq_get(&outq, &l, K_FOREVER);
		for (size_t i = 0; i < sizeof(l.s) && l.s[i]; i++) {
			uart_poll_out(uart, l.s[i]);
		}
	}
}

static void rx_thread(void *a, void *b, void *c)
{
	static char buf[LINE_MAX_IN];
	size_t n = 0;
	bool seen_normal = false;
	uint32_t poll = 0;

	ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
	for (;;) {
		unsigned char ch;
		bool idle = true;

		/* Flashing tools open the port at 1200 baud to request DFU. */
		if ((poll++ & 0x1F) == 0) {
			uint32_t baud = 0;

			if (uart_line_ctrl_get(uart, UART_LINE_CTRL_BAUD_RATE, &baud) == 0) {
				if (baud == 1200 && seen_normal) {
					hostif_reboot_dfu();
				} else if (baud > 1200) {
					seen_normal = true;
				}
			}
		}

		while (uart_poll_in(uart, &ch) == 0) {
			idle = false;
			if (ch == '\r' || ch == '\n') {
				if (n) {
					buf[n] = '\0';
					line_cb(buf);
					n = 0;
				}
			} else if (ch == 0x08 || ch == 0x7F) {
				if (n) {
					n--;
				}
			} else if (n < sizeof(buf) - 1) {
				buf[n++] = (char)ch;
			}
		}
		if (idle) {
			k_msleep(3);
		}
	}
}

K_THREAD_STACK_DEFINE(tx_stack, 1024);
K_THREAD_STACK_DEFINE(rx_stack, 3072);
static struct k_thread tx_thr, rx_thr;

void hostif_init(void (*on_line)(char *line))
{
	uart = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));
	line_cb = on_line;
	k_thread_create(&tx_thr, tx_stack, K_THREAD_STACK_SIZEOF(tx_stack),
			tx_thread, NULL, NULL, NULL, 9, 0, K_NO_WAIT);
	k_thread_name_set(&tx_thr, "host_tx");
	k_thread_create(&rx_thr, rx_stack, K_THREAD_STACK_SIZEOF(rx_stack),
			rx_thread, NULL, NULL, NULL, 10, 0, K_NO_WAIT);
	k_thread_name_set(&rx_thr, "host_rx");
}
