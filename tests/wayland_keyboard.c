/* Persistent, independent Wayland keyboard used only by native acceptance.
 * The fixed evdev/pc105/us map avoids per-command keyboard map replacement.
 * Input is restricted to synthetic ASCII text and named control keys.
 */
#define _GNU_SOURCE
#include <wayland-client.h>
#include <xkbcommon/xkbcommon.h>
#include <linux/input-event-codes.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "virtual-keyboard-unstable-v1-client-protocol.h"

static struct wl_display *display;
static struct wl_seat *seat;
static struct zwp_virtual_keyboard_manager_v1 *manager;
static struct zwp_virtual_keyboard_v1 *keyboard;
static struct xkb_state *key_state;
static unsigned char pressed[KEY_MAX + 1];

static void seat_capabilities(void *data, struct wl_seat *value, uint32_t capabilities) {
    (void)data; (void)value; (void)capabilities;
}
static void seat_name(void *data, struct wl_seat *value, const char *name) {
    (void)data; (void)value; (void)name;
}
static const struct wl_seat_listener seat_listener = {
    .capabilities = seat_capabilities, .name = seat_name,
};

static void registry_global(void *data, struct wl_registry *registry,
                            uint32_t name, const char *interface, uint32_t version) {
    (void)data; (void)version;
    if (!seat && !strcmp(interface, wl_seat_interface.name)) {
        seat = wl_registry_bind(registry, name, &wl_seat_interface, 1);
        wl_seat_add_listener(seat, &seat_listener, NULL);
    } else if (!manager && !strcmp(interface, zwp_virtual_keyboard_manager_v1_interface.name)) {
        manager = wl_registry_bind(registry, name, &zwp_virtual_keyboard_manager_v1_interface, 1);
    }
}
static void registry_remove(void *data, struct wl_registry *registry, uint32_t name) {
    (void)data; (void)registry; (void)name;
}
static const struct wl_registry_listener registry_listener = {
    .global = registry_global, .global_remove = registry_remove,
};

static uint32_t milliseconds(void) {
    struct timespec time;
    clock_gettime(CLOCK_MONOTONIC, &time);
    return (uint32_t)((uint64_t)time.tv_sec * 1000 + (uint64_t)time.tv_nsec / 1000000);
}

static void key_event(uint32_t code, int down) {
    xkb_state_update_key(key_state, code + 8, down ? XKB_KEY_DOWN : XKB_KEY_UP);
    zwp_virtual_keyboard_v1_modifiers(
        keyboard,
        xkb_state_serialize_mods(key_state, XKB_STATE_MODS_DEPRESSED),
        xkb_state_serialize_mods(key_state, XKB_STATE_MODS_LATCHED),
        xkb_state_serialize_mods(key_state, XKB_STATE_MODS_LOCKED),
        xkb_state_serialize_layout(key_state, XKB_STATE_LAYOUT_EFFECTIVE)
    );
    zwp_virtual_keyboard_v1_key(
        keyboard, milliseconds(), code,
        down ? WL_KEYBOARD_KEY_STATE_PRESSED : WL_KEYBOARD_KEY_STATE_RELEASED
    );
    pressed[code] = down != 0;
}

static void tap(uint32_t code) {
    key_event(code, 1);
    key_event(code, 0);
}

static uint32_t ascii_key(unsigned char character) {
    static const uint32_t alphabet[] = {
        KEY_A, KEY_B, KEY_C, KEY_D, KEY_E, KEY_F, KEY_G, KEY_H, KEY_I,
        KEY_J, KEY_K, KEY_L, KEY_M, KEY_N, KEY_O, KEY_P, KEY_Q, KEY_R,
        KEY_S, KEY_T, KEY_U, KEY_V, KEY_W, KEY_X, KEY_Y, KEY_Z,
    };
    if (character >= 'a' && character <= 'z') return alphabet[character - 'a'];
    if (character >= '1' && character <= '9') return KEY_1 + character - '1';
    if (character == '0') return KEY_0;
    if (character == ' ') return KEY_SPACE;
    return KEY_RESERVED;
}

static uint32_t named_key(const char *name) {
    if (strlen(name) == 1) return ascii_key((unsigned char)name[0]);
    if (!strcmp(name, "Escape")) return KEY_ESC;
    if (!strcmp(name, "BackSpace")) return KEY_BACKSPACE;
    if (!strcmp(name, "Tab")) return KEY_TAB;
    if (!strcmp(name, "Return")) return KEY_ENTER;
    if (!strcmp(name, "space")) return KEY_SPACE;
    if (!strcmp(name, "End")) return KEY_END;
    return KEY_RESERVED;
}

static int send_command(const char *line) {
    if (!strncmp(line, "text ", 5)) {
        const unsigned char *text = (const unsigned char *)line + 5;
        if (strlen((const char *)text) > 256) return -1;
        for (const unsigned char *p = text; *p; ++p) if (!ascii_key(*p)) return -1;
        for (const unsigned char *p = text; *p; ++p) {
            tap(ascii_key(*p));
            if (wl_display_flush(display) < 0 && errno != EAGAIN) return -1;
            struct timespec delay = { .tv_sec = 0, .tv_nsec = 35000000 };
            while (nanosleep(&delay, &delay) < 0 && errno == EINTR) {}
        }
    } else if (!strncmp(line, "key ", 4)) {
        uint32_t code = named_key(line + 4);
        if (!code) return -1;
        tap(code);
    } else if (!strncmp(line, "shortcut ", 9)) {
        char modifier[32], key[32], extra;
        if (sscanf(line + 9, "%31s %31s %c", modifier, key, &extra) != 2) return -1;
        uint32_t code = named_key(key);
        uint32_t modifier_code = !strcmp(modifier, "ctrl") ? KEY_LEFTCTRL :
                                 !strcmp(modifier, "shift") ? KEY_LEFTSHIFT : 0;
        if (!code || !modifier_code) return -1;
        key_event(modifier_code, 1);
        tap(code);
        key_event(modifier_code, 0);
    } else {
        return -1;
    }
    return wl_display_roundtrip(display) < 0 ? -1 : 0;
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: wayland-keyboard KEYMAP_OUTPUT\n");
        return 2;
    }
    int status = 1, descriptor = -1;
    struct wl_registry *registry = NULL;
    struct xkb_context *context = NULL;
    struct xkb_keymap *keymap = NULL;
    char *keymap_text = NULL, *line = NULL;
    size_t line_size = 0;
    display = wl_display_connect(NULL);
    if (!display) {
        fprintf(stderr, "cannot connect to the selected Wayland socket\n");
        goto cleanup;
    }
    registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    if (wl_display_roundtrip(display) < 0 || !seat || !manager) {
        fprintf(stderr, "the selected display lacks virtual-keyboard-v1 or a seat\n");
        goto cleanup;
    }
    context = xkb_context_new(XKB_CONTEXT_NO_FLAGS);
    const struct xkb_rule_names names = {
        .rules = "evdev", .model = "pc105", .layout = "us", .variant = "", .options = "",
    };
    if (!context) goto cleanup;
    keymap = xkb_keymap_new_from_names(context, &names, XKB_KEYMAP_COMPILE_NO_FLAGS);
    if (!keymap) goto cleanup;
    key_state = xkb_state_new(keymap);
    if (!key_state) goto cleanup;
    keymap_text = xkb_keymap_get_as_string(keymap, XKB_KEYMAP_FORMAT_TEXT_V1);
    if (!keymap_text) goto cleanup;
    size_t keymap_size = strlen(keymap_text) + 1;
    FILE *keymap_file = fopen(argv[1], "w");
    if (!keymap_file) goto cleanup;
    int saved = fwrite(keymap_text, 1, keymap_size - 1, keymap_file) == keymap_size - 1;
    if (fclose(keymap_file) || !saved) goto cleanup;
    descriptor = memfd_create("ztype-native-test-us-keymap", MFD_CLOEXEC);
    if (descriptor < 0) goto cleanup;
    for (size_t offset = 0; offset < keymap_size;) {
        ssize_t count = write(descriptor, keymap_text + offset, keymap_size - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) goto cleanup;
        offset += (size_t)count;
    }
    keyboard = zwp_virtual_keyboard_manager_v1_create_virtual_keyboard(manager, seat);
    zwp_virtual_keyboard_v1_keymap(
        keyboard, WL_KEYBOARD_KEYMAP_FORMAT_XKB_V1, descriptor, (uint32_t)keymap_size
    );
    if (wl_display_roundtrip(display) < 0) goto cleanup;
    close(descriptor);
    descriptor = -1;
    puts("{\"ready\":true,\"protocol\":\"virtual-keyboard-v1\",\"rules\":\"evdev\",\"model\":\"pc105\",\"layout\":\"us\",\"persistent_keymap\":true}");
    fflush(stdout);
    unsigned int sequence = 0;
    while (getline(&line, &line_size, stdin) >= 0) {
        line[strcspn(line, "\r\n")] = '\0';
        if (!strcmp(line, "quit")) {
            printf("{\"acknowledged\":%u,\"ok\":true}\n", ++sequence);
            fflush(stdout);
            status = 0;
            goto cleanup;
        }
        if (send_command(line) < 0) {
            fprintf(stderr, "invalid synthetic keyboard command or Wayland failure\n");
            goto cleanup;
        }
        printf("{\"acknowledged\":%u,\"ok\":true}\n", ++sequence);
        fflush(stdout);
    }
    status = 0;
cleanup:
    if (keyboard && key_state) {
        for (uint32_t code = 1; code <= KEY_MAX; ++code) {
            if (pressed[code]) key_event(code, 0);
        }
        zwp_virtual_keyboard_v1_modifiers(keyboard, 0, 0, 0, 0);
        zwp_virtual_keyboard_v1_destroy(keyboard);
    }
    if (manager) zwp_virtual_keyboard_manager_v1_destroy(manager);
    if (seat) wl_seat_destroy(seat);
    if (registry) wl_registry_destroy(registry);
    if (display) {
        wl_display_flush(display);
        wl_display_disconnect(display);
    }
    if (descriptor >= 0) close(descriptor);
    free(line);
    free(keymap_text);
    if (key_state) xkb_state_unref(key_state);
    if (keymap) xkb_keymap_unref(keymap);
    if (context) xkb_context_unref(context);
    return status;
}
