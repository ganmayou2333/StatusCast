/*
 * serial_console -- the UART command channel.
 *
 * Commands: help, cfg show|put|reset, net ssid|pass|status, heap, reboot, stats.
 * `cfg put` switches into a paste mode so a multi-line JSON document does not
 * have to survive argv splitting; it ends at a line containing only "EOF".
 *
 * WiFi credentials go to NVS and are never echoed back.
 */
#pragma once

void serial_console_start(void);
