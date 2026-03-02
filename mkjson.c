#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
static void sleep_ms(unsigned long ms) { usleep(ms * 1000UL); }

static unsigned long rng_state = 2463534242UL;
static unsigned long lcg(void) {
	rng_state = (1103515245UL * rng_state + 12345UL) & 0x7fffffffUL;
	return rng_state;
}
static double jitter(double span) {
	double x = (double)lcg() / 1073741823.0;
	if (x > 1.0) x = 1.0;
	return (x * 2.0 - 1.0) * span;
}

static void iso8601_utc(char *buf, size_t bufsz, time_t base, int ms) {
	struct tm g;
#if defined(_WIN32)
	g = *gmtime(&base); /* i can't test this */
#else
	{
		struct tm *pt = gmtime(&base);
		if (pt) g = *pt;
		else memset(&g, 0, sizeof(g));
	}
#endif
	if (ms < 0) ms = 0; if (ms > 999) ms = 999;
	sprintf(buf, "%04d-%02d-%02dT%02d:%02d:%02d.%03dZ",
			g.tm_year + 1900, g.tm_mon + 1, g.tm_mday,
			g.tm_hour, g.tm_min, g.tm_sec, ms);
}

static double clamp(double v, double lo, double hi) {
	if (v < lo) return lo;
	if (v > hi) return hi;
	return v;
}

static int write_json(const char *path,
		time_t base_ts,
		int base_ms,
		double feed_temp,
		double peeler_load,
		double optical_yield,
		double fryer_oil_temp,
		double dryer_humidity,
		double packager_speed,
		double energy_kwh,
		const char *batch1,
		const char *batch2,
		int seq_start) {
	FILE *f = fopen(path, "wb");
	char tbuf[32];
	if (!f) return -1;

	iso8601_utc(tbuf, sizeof(tbuf), base_ts + 0, (base_ms + 120) % 1000);
	fprintf(f,
			"[\n"
			"  {\n"
			"    \"ts\": \"%s\",\n"
			"    \"site_id\": \"SK-LM\",\n"
			"    \"line_id\": \"L1\",\n"
			"    \"asset_id\": \"Intake_Hopper\",\n"
			"    \"signal\": \"feed_temp\",\n"
			"    \"value\": %.1f,\n"
			"    \"unit\": \"\\u00B0C\",\n"
			"    \"quality\": \"%s\",\n"
			"    \"batch_id\": \"%s\",\n"
			"    \"seq\": %d\n"
			"  },\n",
			tbuf, feed_temp,
			(feed_temp >= 18.0 && feed_temp <= 22.0) ? "GOOD" : "WARN",
			batch1, seq_start + 0);

	iso8601_utc(tbuf, sizeof(tbuf), base_ts + 1, (base_ms + 170) % 1000);
	fprintf(f,
			"  {\n"
			"    \"ts\": \"%s\",\n"
			"    \"site_id\": \"SK-LM\",\n"
			"    \"line_id\": \"L1\",\n"
			"    \"asset_id\": \"Washer/Peeler\",\n"
			"    \"signal\": \"peeler_load\",\n"
			"    \"value\": %.1f,\n"
			"    \"unit\": \"t/h\",\n"
			"    \"quality\": \"%s\",\n"
			"    \"batch_id\": \"%s\",\n"
			"    \"seq\": %d\n"
			"  },\n",
			tbuf, peeler_load,
			(peeler_load >= 8.0 && peeler_load <= 12.0) ? "GOOD" : "WARN",
			batch1, seq_start + 1);

	iso8601_utc(tbuf, sizeof(tbuf), base_ts + 2, (base_ms + 205) % 1000);
	fprintf(f,
			"  {\n"
			"    \"ts\": \"%s\",\n"
			"    \"site_id\": \"SK-LM\",\n"
			"    \"line_id\": \"L1\",\n"
			"    \"asset_id\": \"Optical_Sorter\",\n"
			"    \"signal\": \"optical_yield\",\n"
			"    \"value\": %.1f,\n"
			"    \"unit\": \"%%\",\n"
			"    \"quality\": \"%s\",\n"
			"    \"batch_id\": \"%s\",\n"
			"    \"seq\": %d\n"
			"  },\n",
			tbuf, optical_yield,
			(optical_yield >= 96.0) ? "GOOD" : "WARN",
			batch1, seq_start + 2);

	iso8601_utc(tbuf, sizeof(tbuf), base_ts + 3, (base_ms + 260) % 1000);
	fprintf(f,
			"  {\n"
			"    \"ts\": \"%s\",\n"
			"    \"site_id\": \"SK-LM\",\n"
			"    \"line_id\": \"L1\",\n"
			"    \"asset_id\": \"Fryer\",\n"
			"    \"signal\": \"fryer_oil_temp\",\n"
			"    \"value\": %.1f,\n"
			"    \"unit\": \"\\u00B0C\",\n"
			"    \"quality\": \"%s\",\n"
			"    \"batch_id\": \"%s\",\n"
			"    \"seq\": %d\n"
			"  },\n",
			tbuf, fryer_oil_temp,
			(fryer_oil_temp >= 170.0 && fryer_oil_temp <= 185.0) ? "GOOD" : "WARN",
			batch2, seq_start + 3);

	iso8601_utc(tbuf, sizeof(tbuf), base_ts + 4, (base_ms + 315) % 1000);
	fprintf(f,
			"  {\n"
			"    \"ts\": \"%s\",\n"
			"    \"site_id\": \"SK-LM\",\n"
			"    \"line_id\": \"L1\",\n"
			"    \"asset_id\": \"Dryer\",\n"
			"    \"signal\": \"dryer_humidity\",\n"
			"    \"value\": %.1f,\n"
			"    \"unit\": \"%%\",\n"
			"    \"quality\": \"%s\",\n"
			"    \"batch_id\": \"%s\",\n"
			"    \"seq\": %d\n"
			"  },\n",
			tbuf, dryer_humidity,
			(dryer_humidity >= 5.0 && dryer_humidity <= 8.0) ? "GOOD" : "WARN",
			batch2, seq_start + 4);

	iso8601_utc(tbuf, sizeof(tbuf), base_ts + 5, (base_ms + 360) % 1000);
	fprintf(f,
			"  {\n"
			"    \"ts\": \"%s\",\n"
			"    \"site_id\": \"SK-LM\",\n"
			"    \"line_id\": \"L1\",\n"
			"    \"asset_id\": \"Packer\",\n"
			"    \"signal\": \"packager_speed\",\n"
			"    \"value\": %.1f,\n"
			"    \"unit\": \"bags/min\",\n"
			"    \"quality\": \"%s\",\n"
			"    \"batch_id\": \"%s\",\n"
			"    \"seq\": %d\n"
			"  },\n",
			tbuf, packager_speed,
			(packager_speed >= 110.0 && packager_speed <= 130.0) ? "GOOD" : "WARN",
			batch2, seq_start + 5);

	iso8601_utc(tbuf, sizeof(tbuf), base_ts + 6, (base_ms + 415) % 1000);
	fprintf(f,
			"  {\n"
			"    \"ts\": \"%s\",\n"
			"    \"site_id\": \"%s\",\n"
			"    \"line_id\": \"%s\",\n"
			"    \"asset_id\": \"Energy_Center\",\n"
			"    \"signal\": \"energy_kwh\",\n"
			"    \"value\": %.0f,\n"
			"    \"unit\": \"kWh\",\n"
			"    \"quality\": \"%s\",\n"
			"    \"batch_id\": \"%s\",\n"
			"    \"seq\": %d\n"
			"  }\n"
			"]\n",
			tbuf, "SK-LM", "L1", energy_kwh,
			(energy_kwh >= 280.0 && energy_kwh <= 360.0) ? "GOOD" : "WARN",
			batch2, seq_start + 6);

	fclose(f);
	return 0;
}

int main(int argc, char **argv) {
	int i, n = -1;
	time_t start_t;
	int base_ms;
	double feed_temp = 13.2;
	double peeler_load = 9.4;
	double optical_yield = 97.8;
	double fryer_oil_temp = 176.5;
	double dryer_humidity = 6.8;
	double packager_speed = 118.0;
	double energy_kwh = 312.0;

	int seq = 101;
	char batch1[32];
	char batch2[32];

	for (i = 1; i < argc; ++i) {
		if (strcmp(argv[i], "--stop-after") == 0) {
			if (i + 1 < argc) {
				n = atoi(argv[i + 1]);
			}
		}
	}
	if (n <= 0) {
		fprintf(stderr, "usage: %s --stop-after n\n", argv[0]);
		return 1;
	}

	rng_state = (unsigned long)time(0) ^ 0xA5A5A5A5UL;

	start_t = time(0);
	base_ms = 120;

	strcpy(batch1, "B20251216-001");
	strcpy(batch2, "B20251216-002");

	for (i = 0; i < n; ++i) {
		time_t frame_t = start_t + i;
		int ms = (base_ms + (i * 47)) % 1000;

		feed_temp = clamp(feed_temp + jitter(0.3), 10.0, 25.0);
		peeler_load = clamp(peeler_load + jitter(0.6), 5.0, 15.0);
		optical_yield = clamp(optical_yield + jitter(0.5), 92.0, 99.9);
		fryer_oil_temp = clamp(fryer_oil_temp + jitter(1.8), 160.0, 195.0);
		dryer_humidity = clamp(dryer_humidity + jitter(0.4), 3.0, 12.0);
		packager_speed = clamp(packager_speed + jitter(3.0), 90.0, 150.0);
		energy_kwh = clamp(energy_kwh + jitter(12.0), 250.0, 400.0);

		if (write_json("feed.json",
					frame_t, ms,
					feed_temp, peeler_load, optical_yield,
					fryer_oil_temp, dryer_humidity, packager_speed,
					energy_kwh,
					batch1, batch2,
					seq) != 0) {
			fprintf(stderr, "failed to write feed.json\n");
			return 2;
		}

		seq += 7;

		if (i + 1 < n) {
			sleep_ms(1000UL);
		}
	}

	return 0;
}
