#if defined(__linux__)
#include <dirent.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "dm.h"
#define TP_MAX 256
#define TP_SECS 5
typedef struct { long tid; char name[20]; unsigned long long ticks; int cpu; double pct; } TpEntry;
static int tp_read(long tid, TpEntry *e) {
    char path[64], buf[1024];
    snprintf(path, sizeof path, "/proc/self/task/%ld/stat", tid);
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    size_t n = fread(buf, 1, sizeof buf - 1, f);
    fclose(f);
    buf[n] = 0;
    char *lp = strchr(buf, '('), *rp = strrchr(buf, ')');
    if (!lp || !rp || rp < lp) return 0;
    size_t nl = (size_t)(rp - lp - 1);
    if (nl >= sizeof e->name) nl = sizeof e->name - 1;
    memcpy(e->name, lp + 1, nl); e->name[nl] = 0;
    char *p = rp + 2;
    unsigned long long ut = 0, st = 0; int cpu = -1;
    int idx = 0;
    for (char *tok = strtok(p, " "); tok; tok = strtok(NULL, " "), idx++) {
        if (idx == 11) ut = strtoull(tok, NULL, 10);
        else if (idx == 12) st = strtoull(tok, NULL, 10);
        else if (idx == 36) { cpu = atoi(tok); break; }
    }
    e->tid = tid; e->ticks = ut + st; e->cpu = cpu;
    return 1;
}
static int tp_scan(TpEntry *out) {
    DIR *d = opendir("/proc/self/task");
    if (!d) return 0;
    int n = 0;
    struct dirent *de;
    while ((de = readdir(d)) && n < TP_MAX) {
        if (de->d_name[0] < '0' || de->d_name[0] > '9') continue;
        if (tp_read(atol(de->d_name), &out[n])) n++;
    }
    closedir(d);
    return n;
}
static long tp_khz(int cpu) {
    if (cpu < 0) return 0;
    char path[96]; char b[32] = {0};
    snprintf(path, sizeof path, "/sys/devices/system/cpu/cpu%d/cpufreq/scaling_cur_freq", cpu);
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    if (!fgets(b, sizeof b, f)) b[0] = 0;
    fclose(f);
    return atol(b);
}
static int tp_cmp(const void *a, const void *b) {
    double x = ((const TpEntry *)a)->pct, y = ((const TpEntry *)b)->pct;
    return (x < y) - (x > y);
}
static void *tp_thread(void *arg) {
    (void)arg;
    static TpEntry prev[TP_MAX], cur[TP_MAX];
    long hz = sysconf(_SC_CLK_TCK); if (hz <= 0) hz = 100;
    int np = tp_scan(prev);
    for (;;) {
        sleep(TP_SECS);
        int nc = tp_scan(cur);
        for (int i = 0; i < nc; i++) {
            unsigned long long before = 0;
            for (int j = 0; j < np; j++) if (prev[j].tid == cur[i].tid) { before = prev[j].ticks; break; }
            cur[i].pct = 100.0 * (double)(cur[i].ticks - before) / (double)hz / TP_SECS;
        }
        TpEntry sorted[TP_MAX];
        memcpy(sorted, cur, sizeof(TpEntry) * (size_t)nc);
        qsort(sorted, (size_t)nc, sizeof sorted[0], tp_cmp);
        char line[640]; size_t off = 0;
        double total = 0;
        for (int i = 0; i < nc; i++) total += sorted[i].pct;
        off += (size_t)snprintf(line + off, sizeof line - off, "[threads] %d threads, %.0f%% of one core in all; busiest:", nc, total);
        for (int i = 0; i < nc && i < 6 && sorted[i].pct >= 3.0 && off < sizeof line - 80; i++)
            off += (size_t)snprintf(line + off, sizeof line - off, " %s(%ld) %.0f%% cpu%d@%ldMHz;",
                                    sorted[i].name, sorted[i].tid, sorted[i].pct, sorted[i].cpu, tp_khz(sorted[i].cpu) / 1000);
        LOG("%s", line);
        memcpy(prev, cur, sizeof(TpEntry) * (size_t)nc);
        np = nc;
    }
    return NULL;
}
__attribute__((constructor)) static void tp_start(void) {
    const char *e = getenv("DM_THREAD_PROF");
    if (e && e[0] == '0') return;
    pthread_t th;
    if (!pthread_create(&th, NULL, tp_thread, NULL)) pthread_detach(th);
}
#endif
