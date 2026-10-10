/*
 * Persist the configuration to the settings_storage flash partition.
 *
 * One fixed-size record at the start of the partition, CRC-32 protected.  No
 * settings subsystem and no wear levelling: the record is rewritten only when
 * the operator types `save`, which is rare, and a single erase/write of one
 * page is the whole operation.
 */
#include "sim.h"

#include <string.h>
#include <zephyr/storage/flash_map.h>
#include <zephyr/sys/crc.h>

#define STORE_PARTITION settings_storage
#define STORE_ID FIXED_PARTITION_ID(STORE_PARTITION)

static uint32_t record_crc(const struct sim_cfg *c)
{
	/* Everything except the trailing crc32 field. */
	return crc32_ieee((const uint8_t *)c, offsetof(struct sim_cfg, crc32));
}

int store_save(const struct sim_cfg *src)
{
	const struct flash_area *fa;
	struct sim_cfg rec;
	int rc;

	k_mutex_lock(&cfg_lock, K_FOREVER);
	rec = *src;
	k_mutex_unlock(&cfg_lock);
	rec.magic = SIM_MAGIC;
	rec.version = SIM_VERSION;
	rec.size = sizeof(rec);
	rec.crc32 = record_crc(&rec);

	rc = flash_area_open(STORE_ID, &fa);
	if (rc) {
		return rc;
	}
	/* Erase to a page boundary, then write the fixed-size record. */
	size_t esize = fa->fa_size;

	if (esize > 0x1000) {
		esize = 0x1000;        /* one 4 KB page is plenty */
	}
	rc = flash_area_erase(fa, 0, esize);
	if (rc == 0) {
		rc = flash_area_write(fa, 0, &rec, sizeof(rec));
	}
	flash_area_close(fa);
	return rc;
}

int store_load(struct sim_cfg *dst)
{
	const struct flash_area *fa;
	struct sim_cfg rec;
	int rc;

	rc = flash_area_open(STORE_ID, &fa);
	if (rc) {
		return rc;
	}
	rc = flash_area_read(fa, 0, &rec, sizeof(rec));
	flash_area_close(fa);
	if (rc) {
		return rc;
	}
	if (rec.magic != SIM_MAGIC || rec.version != SIM_VERSION ||
	    rec.size != sizeof(rec) || rec.crc32 != record_crc(&rec)) {
		return -ENOENT;
	}
	*dst = rec;
	return 0;
}

int store_erase(void)
{
	const struct flash_area *fa;
	int rc = flash_area_open(STORE_ID, &fa);

	if (rc) {
		return rc;
	}
	size_t esize = fa->fa_size > 0x1000 ? 0x1000 : fa->fa_size;

	rc = flash_area_erase(fa, 0, esize);
	flash_area_close(fa);
	return rc;
}
