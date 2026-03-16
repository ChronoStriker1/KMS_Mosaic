#ifndef APP_H
#define APP_H

#include <signal.h>

#define APP_RUN_RELOAD 75

int app_run(int argc, char **argv, int *debug, volatile sig_atomic_t *stop_flag);

#endif
