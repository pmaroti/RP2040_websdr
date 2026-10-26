/*
 * tusb_config.h - compile-time configuration of the TinyUSB stack.
 *
 * TinyUSB looks for this file by name. It decides which USB device classes
 * are compiled in. Here that is only the network class: the Pico presents
 * itself as a USB Ethernet adapter, in one of two flavours (NCM or
 * RNDIS/ECM, selected below).
 */

#ifndef _TUSB_CONFIG_H_
#define _TUSB_CONFIG_H_

#ifdef __cplusplus
extern "C" {
#endif

// Pulls in TCP_MSS, used for the NCM buffer sizes below.
#include "lwipopts.h"

// Which USB port ("root hub port") runs the device stack. The RP2040 has one.
#ifndef BOARD_TUD_RHPORT
  #define BOARD_TUD_RHPORT 0
#endif

// Highest USB speed to use; the default is whatever the chip supports
// (Full Speed, 12 Mbit/s, on the RP2040).
#ifndef BOARD_TUD_MAX_SPEED
  #define BOARD_TUD_MAX_SPEED OPT_MODE_DEFAULT_SPEED
#endif

// The MCU type is passed in by the Pico SDK's build system.
#ifndef CFG_TUSB_MCU
  #error CFG_TUSB_MCU must be defined
#endif

// No RTOS: TinyUSB is polled with tud_task() from the main loop.
#ifndef CFG_TUSB_OS
  #define CFG_TUSB_OS OPT_OS_NONE
#endif

// TinyUSB debug log level (0 = off).
#ifndef CFG_TUSB_DEBUG
  #define CFG_TUSB_DEBUG 0
#endif

#define CFG_TUD_ENABLED       1     // enable the device (not host) stack
#define CFG_TUD_MAX_SPEED     BOARD_TUD_MAX_SPEED

// Placement and alignment of USB transfer buffers. The RP2040 needs no
// special memory section; 4-byte alignment suits its USB controller.
#ifndef CFG_TUSB_MEM_SECTION
  #define CFG_TUSB_MEM_SECTION
#endif

#ifndef CFG_TUSB_MEM_ALIGN
  #define CFG_TUSB_MEM_ALIGN __attribute__((aligned(4)))
#endif

// Packet size of the control endpoint 0 (the maximum for Full Speed).
#ifndef CFG_TUD_ENDPOINT0_SIZE
  #define CFG_TUD_ENDPOINT0_SIZE 64
#endif

// NCM transfer block sizes, must be larger than the MTU.
// NCM can pack several Ethernet frames into one USB "transfer block" (NTB).
// IN = Pico to host, OUT = host to Pico. _N is how many blocks are buffered;
// two IN blocks let the stack fill one while the other is being sent.
#define CFG_TUD_NCM_IN_NTB_MAX_SIZE  (2 * TCP_MSS + 100)
#define CFG_TUD_NCM_OUT_NTB_MAX_SIZE (2 * TCP_MSS + 100)
#define CFG_TUD_NCM_OUT_NTB_N 1
#define CFG_TUD_NCM_IN_NTB_N  2

// The network class has two drivers, only one can be enabled:
//   NCM        - Linux, macOS, Windows 11 (default, fastest)
//   RNDIS/ECM  - Windows 10 (RNDIS) plus Linux/macOS (ECM)
// Select RNDIS/ECM with -DUSB_NET_RNDIS=ON at CMake time.
// (CMakeLists.txt turns that option into USE_RNDIS=1.)
#ifndef USE_RNDIS
  #define USE_RNDIS 0
#endif
#define CFG_TUD_ECM_RNDIS     USE_RNDIS
#define CFG_TUD_NCM           (1 - CFG_TUD_ECM_RNDIS)

#ifdef __cplusplus
}
#endif

#endif /* _TUSB_CONFIG_H_ */
