#define _GNU_SOURCE

#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#ifdef __linux__
#include <execinfo.h>
#endif

#include "app.h"

static int g_debug = 0;
static volatile sig_atomic_t g_stop = 0;

static void handle_stop(int sig) {
    (void)sig;
    g_stop = 1;
}

static void dump_bt_and_exit(int sig) {
    fprintf(stderr, "\nCaught signal %d. Dumping backtrace...\n", sig);
#ifdef __linux__
    void *buf[64];
    int n = backtrace(buf, 64);
    backtrace_symbols_fd(buf, n, 2);
#endif
    _exit(128 + sig);
}

static void install_signal_handlers(void) {
    struct sigaction crash = {0};
    crash.sa_handler = dump_bt_and_exit;
    crash.sa_flags = SA_RESETHAND;
    sigaction(SIGSEGV, &crash, NULL);
    sigaction(SIGABRT, &crash, NULL);
    sigaction(SIGFPE, &crash, NULL);
    sigaction(SIGILL, &crash, NULL);

    struct sigaction stop = {0};
    stop.sa_handler = handle_stop;
    sigaction(SIGTERM, &stop, NULL);
}

int main(int argc, char **argv) {
    install_signal_handlers();
    if (getenv("KMS_MPV_DEBUG")) g_debug = 1;
    setenv("mesa_glthread", "false", 0);
    setenv("MESA_GLTHREAD", "0", 0);
    return app_run(argc, argv, &g_debug, &g_stop);
}
