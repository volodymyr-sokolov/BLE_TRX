/*
 * ble_trx entry point.
 *
 * Brings up USB, loads any saved configuration, and -- like the sibling
 * adaptive-protocol firmware -- reboots into the bootloader on a fatal fault
 * so a board whose only link is USB can always be reflashed over the same
 * cable it failed on.
 */
#include "sim.h"

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/usb/usb_device.h>
#include <zephyr/fatal.h>

static const struct gpio_dt_spec led =
	GPIO_DT_SPEC_GET_OR(DT_ALIAS(led0), gpios, {0});

void k_sys_fatal_error_handler(unsigned int reason, const struct arch_esf *esf)
{
	ARG_UNUSED(esf);
	hostif_printf("{\"ev\":\"fatal\",\"reason\":%u,\"thread\":\"%s\"}",
		      reason, k_thread_name_get(k_current_get()));
	k_busy_wait(50000);
	hostif_reboot_dfu();
	CODE_UNREACHABLE;
}

int main(void)
{
#if defined(CONFIG_USB_DEVICE_STACK)
	usb_enable(NULL);
#endif
	/* Let the host enumerate before the banner, or it lands in a port
	 * nobody has opened yet. */
	k_msleep(1500);

	if (led.port && device_is_ready(led.port)) {
		gpio_pin_configure_dt(&led, GPIO_OUTPUT_INACTIVE);
	}

	cfg_defaults(&cfg);
	bool loaded = store_load(&cfg) == 0;

	hostif_init(cmd_line);
	adv_init();
	rx_init();

	hostif_printf(
		"{\"ev\":\"boot\",\"fw\":\"ble_trx\",\"ver\":%u,\"board\":\"%s\","
		"\"loaded\":%d}",
		SIM_VERSION, CONFIG_BOARD, loaded);
	hostif_printf("type 'help' for commands, 'info' for the current configuration");

	if (cfg.autostart == AUTOSTART_TX) {
		adv_start(0);
		hostif_printf("{\"ev\":\"autostart\",\"mode\":\"tx\"}");
	} else if (cfg.autostart == AUTOSTART_RX) {
		rx_start();
		hostif_printf("{\"ev\":\"autostart\",\"mode\":\"rx\"}");
	}

	for (;;) {
		if (led.port && device_is_ready(led.port)) {
			/* Slow blink idle, fast while advertising, medium while
			 * receiving. */
			gpio_pin_toggle_dt(&led);
		}
		k_msleep(stats.running ? 120 : rxstats.running ? 300 : 600);
	}
	return 0;
}
