/* An editable Wayland application for Ztype's interactive trial.
 * User text stays in memory. --state is an explicit acceptance-test facility.
 */
#include <gtk/gtk.h>
#include <gdk/gdkwayland.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static GtkWidget *window, *entry;
static const char *state_path;
static char *preedit;
static gboolean closed;
static guint64 revision;

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

static void snapshot(void) {
    if (!state_path || !entry) return;
    GString *out = g_string_new("{\"schema\":\"ztype.interactive-trial.v1\",");
    g_string_append_printf(out,
        "\"pid\":%ld,\"revision\":%" G_GUINT64_FORMAT
        ",\"backend\":\"%s\",\"closed\":%s,\"text\":",
        (long)getpid(), ++revision,
        GDK_IS_WAYLAND_DISPLAY(gdk_display_get_default()) ? "wayland" : "other",
        closed ? "true" : "false");
    json_string(out, gtk_entry_get_text(GTK_ENTRY(entry)));
    g_string_append(out, ",\"preedit\":");
    json_string(out, preedit);
    g_string_append(out, "}\n");
    GError *error = NULL;
    if (!g_file_set_contents(state_path, out->str, (gssize)out->len, &error)) {
        fprintf(stderr, "trial state: %s\n", error->message);
        g_error_free(error);
    }
    g_string_free(out, TRUE);
}

static void changed(GtkEditable *widget, gpointer unused) {
    (void)widget; (void)unused;
    snapshot();
}

static void preedit_changed(GtkEntry *widget, const gchar *text, gpointer unused) {
    (void)widget; (void)unused;
    g_free(preedit);
    preedit = g_strdup(text);
    snapshot();
}

static void clear_text(GtkButton *button, gpointer unused) {
    (void)button; (void)unused;
    gtk_entry_reset_im_context(GTK_ENTRY(entry));
    gtk_entry_set_text(GTK_ENTRY(entry), "");
    gtk_widget_grab_focus(entry);
}

static void close_window(GtkButton *button, gpointer unused) {
    (void)button; (void)unused;
    gtk_widget_destroy(window);
}

static gboolean key_pressed(GtkWidget *widget, GdkEventKey *event, gpointer unused) {
    (void)widget; (void)unused;
    if ((event->state & GDK_CONTROL_MASK) &&
            (event->keyval == GDK_KEY_q || event->keyval == GDK_KEY_Q)) {
        gtk_widget_destroy(window);
        return TRUE;
    }
    return FALSE;
}

static void destroyed(GtkWidget *widget, gpointer unused) {
    (void)widget; (void)unused;
    closed = TRUE;
    snapshot();
    gtk_main_quit();
}

static GtkWidget *label(const char *text, const char *name) {
    GtkWidget *widget = gtk_label_new(text);
    gtk_widget_set_name(widget, name);
    gtk_label_set_xalign(GTK_LABEL(widget), 0.0f);
    gtk_label_set_line_wrap(GTK_LABEL(widget), TRUE);
    return widget;
}

int main(int argc, char **argv) {
    if (argc == 3 && !strcmp(argv[1], "--state")) state_path = argv[2];
    else if (argc != 1) {
        fprintf(stderr, "usage: trial-receiver [--state PATH]\n");
        return 2;
    }
    gtk_init(NULL, NULL);
    if (!GDK_IS_WAYLAND_DISPLAY(gdk_display_get_default())) {
        fprintf(stderr, "Ztype trial requires a native Wayland GTK display\n");
        return 1;
    }
    g_set_prgname("ztype-trial");
    g_set_application_name("Ztype 输入试用");
    const char *engine = g_getenv("ZTYPE_TRIAL_ENGINE");
    gboolean kana = engine && !strcmp(engine, "kana");
    gboolean table = engine && !strcmp(engine, "table");
    GtkCssProvider *css = gtk_css_provider_new();
    const char *style =
        "window { background: #101214; color: #f0f1f2; }"
        "* { font-family: sans-serif; }"
        "#brand { font-size: 18px; font-weight: 600; letter-spacing: 1px; }"
        "#heading { font-size: 31px; font-weight: 600; }"
        "#subtitle { font-size: 15px; color: #a5abb1; }"
        "#examples { font-size: 15px; color: #b8bec4; }"
        "#keys { font-size: 13px; color: #a5abb1; }"
        "#footnote { font-size: 12px; color: #7e858d; }"
        "entry { background: #191c20; color: #f2f3f4; font-size: 25px;"
        " padding: 22px; border: 1px solid #30353a; border-radius: 10px; box-shadow: none; }"
        "entry:focus { border-color: #90979e; box-shadow: none; }"
        "entry selection { background: #545c65; color: white; }"
        "button { background: transparent; color: #b8bec4; border: 0; box-shadow: none;"
        " padding: 10px 14px; border-radius: 8px; font-size: 13px; }"
        "button:hover { background: #252a2f; color: white; }";
    GError *error = NULL;
    if (!gtk_css_provider_load_from_data(css, style, -1, &error)) {
        fprintf(stderr, "trial style: %s\n", error->message);
        g_error_free(error);
        return 1;
    }
    gtk_style_context_add_provider_for_screen(
        gdk_screen_get_default(), GTK_STYLE_PROVIDER(css), GTK_STYLE_PROVIDER_PRIORITY_APPLICATION);
    g_object_unref(css);

    window = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    gtk_window_set_title(GTK_WINDOW(window), state_path ? "Ztype · 自动输入验收" : "Ztype · 输入试用");
    gtk_window_set_default_size(GTK_WINDOW(window), 940, 560);
    gtk_window_set_icon_name(GTK_WINDOW(window), "input-keyboard");
    gtk_window_set_decorated(GTK_WINDOW(window), FALSE);
    GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    gtk_container_set_border_width(GTK_CONTAINER(box), 44);
    gtk_container_add(GTK_CONTAINER(window), box);
    GtkWidget *header = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_box_pack_start(GTK_BOX(header), label("ZTYPE", "brand"), TRUE, TRUE, 0);
    GtkWidget *close = gtk_button_new_with_label("结束试用");
    gtk_box_pack_end(GTK_BOX(header), close, FALSE, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(box), header, FALSE, FALSE, 0);
    GtkWidget *heading = label(kana ? "从一句「こんにちは」开始。" : table ? "用自己的码表输入。" : "从一句「你好」开始。", "heading");
    gtk_widget_set_margin_top(heading, 46);
    gtk_box_pack_start(GTK_BOX(box), heading, FALSE, FALSE, 0);
    GtkWidget *subtitle = label(kana ? "输入罗马字，按空格确认。Tab 可以切换平假名和片假名。" : table ? "输入码表中的编码，按空格确认选中的文字。" : "输入拼音，按空格确认。文字会直接进入下方输入框。", "subtitle");
    gtk_widget_set_margin_top(subtitle, 12);
    gtk_box_pack_start(GTK_BOX(box), subtitle, FALSE, FALSE, 0);
    entry = gtk_entry_new();
    gtk_entry_set_placeholder_text(GTK_ENTRY(entry), kana ? "试试 konnichiha" : table ? "输入你的编码" : "试试 nihao");
    gtk_widget_set_margin_top(entry, 30);
    gtk_box_pack_start(GTK_BOX(box), entry, FALSE, FALSE, 0);
    GtkWidget *examples = label(kana ? "konnichiha → こんにちは     nihongo → にほんご" : table ? "候选来自你选择的码表。" : "nihao → 你好     shijie → 世界     zhongwenshurufa → 中文输入法", "examples");
    gtk_widget_set_margin_top(examples, 20);
    gtk_box_pack_start(GTK_BOX(box), examples, FALSE, FALSE, 0);
    GtkWidget *keys = label("Space 确认    Tab 换词    Esc 取消    Ctrl+Space 中英切换    Ctrl+Q 退出", "keys");
    gtk_widget_set_margin_top(keys, 26);
    gtk_box_pack_start(GTK_BOX(box), keys, FALSE, FALSE, 0);
    GtkWidget *footer = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 12);
    gtk_widget_set_vexpand(footer, TRUE);
    gtk_widget_set_valign(footer, GTK_ALIGN_END);
    gtk_widget_set_margin_top(footer, 30);
    gtk_box_pack_start(GTK_BOX(footer),
        label(state_path ? "自动输入验收进行中" : "独立试用窗口 · 关闭后退出 · 输入内容不保存", "footnote"), TRUE, TRUE, 0);
    GtkWidget *clear = gtk_button_new_with_label("清空");
    gtk_box_pack_end(GTK_BOX(footer), clear, FALSE, FALSE, 0);
    gtk_box_pack_end(GTK_BOX(box), footer, TRUE, TRUE, 0);

    preedit = g_strdup("");
    g_signal_connect(entry, "changed", G_CALLBACK(changed), NULL);
    g_signal_connect(entry, "preedit-changed", G_CALLBACK(preedit_changed), NULL);
    g_signal_connect(clear, "clicked", G_CALLBACK(clear_text), NULL);
    g_signal_connect(close, "clicked", G_CALLBACK(close_window), NULL);
    g_signal_connect(window, "key-press-event", G_CALLBACK(key_pressed), NULL);
    g_signal_connect(window, "destroy", G_CALLBACK(destroyed), NULL);
    gtk_widget_show_all(window);
    gtk_widget_grab_focus(entry);
    snapshot();
    gtk_main();
    g_free(preedit);
    return 0;
}
