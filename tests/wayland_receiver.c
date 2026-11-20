/* Real native-Wayland GTK receiver for scripts/test_wayland.py.
 * Control input can clear or focus fields; it cannot insert arbitrary text.
 */
#include <gtk/gtk.h>
#include <gdk/gdkwayland.h>
#include <glib-unix.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

typedef struct {
    GtkWidget *widget;
    const char *name;
    char *preedit;
    guint preedit_changes;
    guint presses;
    guint releases;
    guint focus_in_events;
    guint focus_out_events;
    guint last_press_keyval;
    guint last_press_modifiers;
    guint last_hardware_keycode;
    guint last_group;
    guint last_translated_keyval;
    guint last_consumed_modifiers;
} Entry;

static Entry normal_entry, password_entry;
static GtkWidget *window;
static const char *result_path;
static guint64 revision;
static guint64 acknowledged;
static const char *last_command = "startup";
static gboolean closed;

static void json_string(GString *out, const char *text) {
    g_string_append_c(out, '"');
    for (const unsigned char *p = (const unsigned char *)(text ? text : ""); *p; ++p) {
        switch (*p) {
        case '"': g_string_append(out, "\\\""); break;
        case '\\': g_string_append(out, "\\\\"); break;
        case '\n': g_string_append(out, "\\n"); break;
        case '\r': g_string_append(out, "\\r"); break;
        case '\t': g_string_append(out, "\\t"); break;
        default:
            if (*p < 0x20) g_string_append_printf(out, "\\u%04x", *p);
            else g_string_append_c(out, (char)*p);
        }
    }
    g_string_append_c(out, '"');
}

static void append_entry(GString *out, Entry *entry) {
    gint selection_start = 0, selection_end = 0;
    gboolean selected = gtk_editable_get_selection_bounds(
        GTK_EDITABLE(entry->widget), &selection_start, &selection_end);
    g_string_append(out, "{\"text\":");
    json_string(out, gtk_entry_get_text(GTK_ENTRY(entry->widget)));
    g_string_append(out, ",\"preedit\":");
    json_string(out, entry->preedit);
    g_string_append_printf(out,
        ",\"focused\":%s,\"editable\":%s,\"preedit_changes\":%u,\"key_presses\":%u,\"key_releases\":%u",
        gtk_widget_has_focus(entry->widget) ? "true" : "false",
        gtk_editable_get_editable(GTK_EDITABLE(entry->widget)) ? "true" : "false",
        entry->preedit_changes, entry->presses, entry->releases);
    g_string_append_printf(out, ",\"focus_in_events\":%u,\"focus_out_events\":%u",
        entry->focus_in_events, entry->focus_out_events);
    g_string_append_printf(out,
        ",\"selection\":%s,\"selection_start\":%d,\"selection_end\":%d,\"cursor\":%d,\"last_press_keyval\":%u,\"last_press_modifiers\":%u,\"hardware_keycode\":%u,\"key_group\":%u,\"translated_keyval\":%u,\"consumed_modifiers\":%u}",
        selected ? "true" : "false", selection_start, selection_end,
        gtk_editable_get_position(GTK_EDITABLE(entry->widget)),
        entry->last_press_keyval, entry->last_press_modifiers,
        entry->last_hardware_keycode, entry->last_group,
        entry->last_translated_keyval, entry->last_consumed_modifiers);
}


static void trace_entry(const char *event, Entry *entry) {
    if (!g_getenv("ZTYPE_RECEIVER_TRACE")) return;
    static gboolean first = TRUE;
    static guint64 sequence;
    gchar *path = g_strconcat(result_path, ".events.jsonl", NULL);
    FILE *stream = fopen(path, first ? "w" : "a");
    first = FALSE;
    g_free(path);
    if (!stream) return;
    GString *out = g_string_new("{\"event\":");
    json_string(out, event);
    g_string_append_printf(out,
        ",\"pid\":%ld,\"sequence\":%" G_GUINT64_FORMAT ",\"monotonic_us\":%" G_GINT64_FORMAT ",\"field\":",
        (long)getpid(), ++sequence, g_get_monotonic_time());
    json_string(out, entry->name);
    g_string_append(out, ",\"state\":");
    append_entry(out, entry);
    g_string_append(out, "}\n");
    fputs(out->str, stream);
    fclose(stream);
    g_string_free(out, TRUE);
}

static gboolean trace_key_idle(gpointer data) {
    trace_entry("key-press-idle", data);
    return G_SOURCE_REMOVE;
}

static void selection_changed(GObject *widget, GParamSpec *property, gpointer data) {
    (void)widget; (void)property;
    trace_entry("selection-notify", data);
}

static void snapshot(void) {
    if (!normal_entry.widget || !password_entry.widget) return;
    GString *out = g_string_new("{\"schema\":\"ztype.wayland-receiver.v1\",");
    g_string_append_printf(out,
        "\"pid\":%ld,\"revision\":%" G_GUINT64_FORMAT ",\"acknowledged\":%" G_GUINT64_FORMAT ",\"backend\":\"%s\",\"closed\":%s,\"last_command\":",
        (long)getpid(), ++revision, acknowledged,
        GDK_IS_WAYLAND_DISPLAY(gdk_display_get_default()) ? "wayland" : "other",
        closed ? "true" : "false");
    json_string(out, last_command);
    g_string_append(out, ",\"im_module\":");
    json_string(out, g_getenv("GTK_IM_MODULE"));
    g_string_append(out, ",\"normal\":");
    append_entry(out, &normal_entry);
    g_string_append(out, ",\"password\":");
    append_entry(out, &password_entry);
    g_string_append(out, "}\n");
    GError *error = NULL;
    if (!g_file_set_contents(result_path, out->str, (gssize)out->len, &error)) {
        fprintf(stderr, "receiver snapshot failed: %s\n", error->message);
        g_error_free(error);
        gtk_main_quit();
    }
    g_string_free(out, TRUE);
}

static gboolean snapshot_idle(gpointer unused) {
    (void)unused;
    snapshot();
    return G_SOURCE_REMOVE;
}

static void changed(GtkEditable *widget, gpointer data) {
    (void)widget; (void)data;
    snapshot();
}

static void preedit_changed(GtkEntry *widget, const gchar *preedit, gpointer data) {
    (void)widget;
    Entry *entry = data;
    trace_entry("preedit-before", entry);
    g_free(entry->preedit);
    entry->preedit = g_strdup(preedit);
    ++entry->preedit_changes;
    trace_entry("preedit-after", entry);
    snapshot();
}

static gboolean focus_changed(GtkWidget *widget, GdkEventFocus *event, gpointer data) {
    (void)widget;
    Entry *entry = data;
    if (event->in) ++entry->focus_in_events;
    else ++entry->focus_out_events;
    g_idle_add(snapshot_idle, NULL);
    return FALSE;
}

static gboolean key_event(GtkWidget *widget, GdkEventKey *event, gpointer data) {
    (void)widget;
    Entry *entry = data;
    if (event->type == GDK_KEY_PRESS) {
        ++entry->presses;
        entry->last_press_keyval = event->keyval;
        entry->last_press_modifiers = event->state;
        entry->last_hardware_keycode = event->hardware_keycode;
        entry->last_group = event->group;
        GdkModifierType consumed = 0;
        gint effective_group = 0, level = 0;
        guint translated = 0;
        gdk_keymap_translate_keyboard_state(
            gdk_keymap_get_for_display(gtk_widget_get_display(widget)),
            event->hardware_keycode, event->state, event->group,
            &translated, &effective_group, &level, &consumed);
        entry->last_translated_keyval = translated;
        entry->last_consumed_modifiers = consumed;
        trace_entry("key-press-before", entry);
        g_idle_add(trace_key_idle, entry);
    }
    if (event->type == GDK_KEY_RELEASE) ++entry->releases;
    g_idle_add(snapshot_idle, NULL);
    return FALSE;
}

static void destroy(GtkWidget *widget, gpointer data) {
    (void)widget; (void)data;
    closed = TRUE;
    snapshot();
    gtk_main_quit();
}

static gboolean read_control(GIOChannel *channel, GIOCondition condition, gpointer unused) {
    (void)unused;
    if (condition & (G_IO_HUP | G_IO_ERR)) {
        gtk_widget_destroy(window);
        return G_SOURCE_REMOVE;
    }
    gchar *line = NULL;
    gsize length = 0;
    GError *error = NULL;
    GIOStatus status = g_io_channel_read_line(channel, &line, &length, NULL, &error);
    if (status == G_IO_STATUS_ERROR) {
        fprintf(stderr, "receiver command failed: %s\n", error->message);
        g_error_free(error);
        gtk_widget_destroy(window);
        return G_SOURCE_REMOVE;
    }
    if (status == G_IO_STATUS_EOF) {
        gtk_widget_destroy(window);
        return G_SOURCE_REMOVE;
    }
    if (status != G_IO_STATUS_NORMAL) return G_SOURCE_CONTINUE;
    g_strchomp(line);
    if (!strcmp(line, "normal")) {
        last_command = "normal";
        gtk_widget_grab_focus(normal_entry.widget);
    } else if (!strcmp(line, "password")) {
        last_command = "password";
        gtk_widget_grab_focus(password_entry.widget);
    } else if (!strcmp(line, "clear")) {
        last_command = "clear";
        gtk_entry_reset_im_context(GTK_ENTRY(normal_entry.widget));
        gtk_entry_reset_im_context(GTK_ENTRY(password_entry.widget));
        gtk_entry_set_text(GTK_ENTRY(normal_entry.widget), "");
        gtk_entry_set_text(GTK_ENTRY(password_entry.widget), "");
    } else if (!strcmp(line, "external-edit")) {
        last_command = "external-edit";
        /* Application-originated edit: fixed ASCII, with no explicit IM reset. */
        gtk_entry_set_text(GTK_ENTRY(normal_entry.widget), "application-edit");
        gtk_editable_set_position(GTK_EDITABLE(normal_entry.widget), -1);
    } else if (!strcmp(line, "cursor-reset")) {
        last_command = "cursor-reset";
        /* Public GTK editing action marks the IM context as needing reset. */
        g_signal_emit_by_name(normal_entry.widget, "move-cursor",
                             GTK_MOVEMENT_BUFFER_ENDS, 1, FALSE);
    } else if (!strcmp(line, "readonly")) {
        last_command = "readonly";
        gtk_editable_set_editable(GTK_EDITABLE(normal_entry.widget), FALSE);
    } else if (!strcmp(line, "writable")) {
        last_command = "writable";
        gtk_editable_set_editable(GTK_EDITABLE(normal_entry.widget), TRUE);
    } else if (!strcmp(line, "snapshot")) {
        last_command = "snapshot";
    } else if (!strcmp(line, "quit")) {
        last_command = "quit";
        ++acknowledged;
        g_free(line);
        gtk_widget_destroy(window);
        return G_SOURCE_REMOVE;
    } else {
        fprintf(stderr, "unknown receiver control command\n");
        g_free(line);
        return G_SOURCE_CONTINUE;
    }
    ++acknowledged;
    snapshot();
    g_free(line);
    return G_SOURCE_CONTINUE;
}

static void prepare_entry(Entry *entry, const char *name) {
    entry->name = name;
    entry->widget = gtk_entry_new();
    entry->preedit = g_strdup("");
    gtk_entry_set_placeholder_text(GTK_ENTRY(entry->widget), name);
    gtk_widget_set_hexpand(entry->widget, TRUE);
    g_signal_connect(entry->widget, "notify::selection-bound", G_CALLBACK(selection_changed), entry);
    g_signal_connect(entry->widget, "notify::cursor-position", G_CALLBACK(selection_changed), entry);
    g_signal_connect(entry->widget, "changed", G_CALLBACK(changed), entry);
    g_signal_connect(entry->widget, "preedit-changed", G_CALLBACK(preedit_changed), entry);
    g_signal_connect(entry->widget, "focus-in-event", G_CALLBACK(focus_changed), entry);
    g_signal_connect(entry->widget, "focus-out-event", G_CALLBACK(focus_changed), entry);
    g_signal_connect(entry->widget, "key-press-event", G_CALLBACK(key_event), entry);
    g_signal_connect(entry->widget, "key-release-event", G_CALLBACK(key_event), entry);
}

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    result_path = argv[1];
    const char *title = argv[2];
    gtk_init(&argc, &argv);
    if (!GDK_IS_WAYLAND_DISPLAY(gdk_display_get_default())) {
        fprintf(stderr, "receiver requires an actual Wayland display\n");
        return 3;
    }
    window = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    gtk_window_set_title(GTK_WINDOW(window), title);
    gtk_window_set_default_size(GTK_WINDOW(window), 680, 220);
    GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 16);
    gtk_container_set_border_width(GTK_CONTAINER(box), 24);
    gtk_container_add(GTK_CONTAINER(window), box);
    GtkWidget *label = gtk_label_new("ztype · native Wayland input verification");
    gtk_label_set_xalign(GTK_LABEL(label), 0);
    gtk_box_pack_start(GTK_BOX(box), label, FALSE, FALSE, 0);
    prepare_entry(&normal_entry, "Normal text");
    prepare_entry(&password_entry, "Synthetic password test");
    gtk_entry_set_visibility(GTK_ENTRY(password_entry.widget), FALSE);
    gtk_entry_set_input_purpose(GTK_ENTRY(password_entry.widget), GTK_INPUT_PURPOSE_PASSWORD);
    gtk_entry_set_input_hints(GTK_ENTRY(password_entry.widget),
        GTK_INPUT_HINT_NO_SPELLCHECK | GTK_INPUT_HINT_NO_EMOJI);
    gtk_box_pack_start(GTK_BOX(box), normal_entry.widget, FALSE, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(box), password_entry.widget, FALSE, FALSE, 0);
    g_signal_connect(window, "destroy", G_CALLBACK(destroy), NULL);
    GIOChannel *commands = g_io_channel_unix_new(STDIN_FILENO);
    g_io_channel_set_encoding(commands, NULL, NULL);
    g_io_channel_set_flags(commands, G_IO_FLAG_NONBLOCK, NULL);
    g_io_add_watch(commands, G_IO_IN | G_IO_HUP | G_IO_ERR, read_control, NULL);
    gtk_widget_show_all(window);
    gtk_widget_grab_focus(normal_entry.widget);
    gtk_window_present(GTK_WINDOW(window));
    snapshot();
    puts("{\"ready\":true,\"backend\":\"wayland\"}");
    fflush(stdout);
    gtk_main();
    g_io_channel_unref(commands);
    g_free(normal_entry.preedit);
    g_free(password_entry.preedit);
    return 0;
}
