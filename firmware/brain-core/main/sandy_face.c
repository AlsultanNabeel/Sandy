// Sandy's face — LVGL native objects + animation engine.
//
// Unlike the old immediate-mode (TFT_eSprite) face, here every feature is a
// real LVGL object: eyes/iris/pupil/brows/mouth. That buys us smooth blinks
// and eye movement WITHOUT full-screen flicker (LVGL only repaints the region
// that changed), vertical gradients for a glossy 3-D look, and eased mood
// transitions. Solid shapes only — never a thin hairline.

#include "sandy_face.h"
#include "config.h"
#include "esp_log.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_lcd_panel_ops.h"
#include "esp_timer.h"
#include "esp_random.h"
#include "driver/spi_master.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lvgl.h"

static const char *TAG = "face";

// ─── Display + LVGL internals ─────────────────────────────────────────────────
#define LVGL_TICK_PERIOD_MS     2
#define LVGL_TASK_STACK         6144
#define LVGL_TASK_PRIORITY      5
#define LCD_BUF_LINES           40

static esp_lcd_panel_handle_t s_panel = NULL;
static lv_disp_t             *s_disp  = NULL;
static lv_disp_drv_t          s_drv;
static lv_disp_draw_buf_t     s_draw_buf;
static lv_color_t             s_buf1[TFT_WIDTH * LCD_BUF_LINES];
static lv_color_t             s_buf2[TFT_WIDTH * LCD_BUF_LINES];
static SemaphoreHandle_t      s_mutex;

// ─── Face geometry (240×240) ──────────────────────────────────────────────────
#define EYE_W       98           // round eyes (W == H → perfect circle)
#define EYE_H       98
#define EYE_CY      92           // vertical centre of the eyes
#define BROW_BASE_Y (EYE_CY - EYE_H / 2 - 20)
#define EYE_DX      54           // each eye offset from screen centre
#define IRIS_D      84           // huge iris — white sclera is just a thin rim
#define PUPIL_D     38
#define GLOSS_D     18
#define BROW_W      60
#define BROW_H      12

// ─── Colours ──────────────────────────────────────────────────────────────────
#define C_BG        lv_color_hex(0x000000)
#define C_SCLERA    lv_color_hex(0xFFFFFF)
#define C_SCLERA2   lv_color_hex(0xD7E2F2)
#define C_PUPIL     lv_color_hex(0x05060A)
#define C_GLOSS     lv_color_hex(0xFFFFFF)
#define C_WHITE     lv_color_hex(0xFFFFFF)
#define C_BLUSH     lv_color_hex(0xFF7E9B)

#define IR_BLUE     0x2A6BFF
#define IR_GREEN    0x21D07A
#define IR_AMBER    0xFFB020
#define IR_RED      0xFF3B30
#define IR_PINK     0xFF5FA0
#define IR_SKY      0x35C6FF

// ─── Face objects ─────────────────────────────────────────────────────────────
static lv_obj_t *s_eye_l, *s_eye_r;          // sclera
static lv_obj_t *s_iris_l, *s_iris_r;        // iris (carries pupil + gloss)
static lv_obj_t *s_brow_l, *s_brow_r;
static lv_obj_t *s_mouth_arc;                // smile / frown
static lv_obj_t *s_mouth_bar;                // neutral / flat
static lv_obj_t *s_mouth_o;                  // open (surprised)
static lv_obj_t *s_blush_l, *s_blush_r;

static volatile sandy_mood_t s_mood = MOOD_IDLE;
static int16_t  s_eye_target_h = EYE_H;
static int16_t  s_eye_l_x0, s_eye_r_x0;   // resting eye positions
static int64_t  s_look_until_ms;          // hold gaze toward a sound until this time
static volatile int64_t s_last_active_ms; // last interaction, for the sleep timer

// ─── Mood → look ──────────────────────────────────────────────────────────────
typedef enum { MO_NEUTRAL, MO_SMILE, MO_BIG_SMILE, MO_FROWN, MO_OPEN, MO_FLAT, MO_SMIRK } mouth_t;

typedef struct {
    uint8_t  openness;   // 30-100 → eye height
    int16_t  brow_deg;   // inner-end tilt; + = angry (inner down), - = sad (inner up)
    int8_t   brow_dy;    // whole-brow vertical shift: - = raised, + = lowered toward eyes
    mouth_t  mouth;
    uint32_t iris;
    bool     blush;
} look_t;

static const look_t LOOKS[MOOD_COUNT] = {
    [MOOD_IDLE]         = {100,   0,   0, MO_NEUTRAL,   IR_BLUE,  false},
    [MOOD_HAPPY]        = {100, -10,  -2, MO_SMILE,     IR_GREEN, true },
    [MOOD_CURIOUS]      = {100,  -8,  -6, MO_SMILE,     IR_AMBER, false},
    [MOOD_SAD]          = { 85, -16,  -2, MO_FROWN,     IR_SKY,   false},
    [MOOD_ALERT]        = {100,  -6, -10, MO_OPEN,      IR_RED,   false},
    [MOOD_SURPRISED]    = {100,  -6, -12, MO_OPEN,      IR_SKY,   false},
    [MOOD_BIG_HAPPY]    = {100, -12,  -3, MO_BIG_SMILE, IR_GREEN, true },
    [MOOD_FOCUSED]      = { 72,  12,   5, MO_FLAT,      IR_BLUE,  false},
    [MOOD_BORED]        = { 55,   4,   6, MO_NEUTRAL,   IR_SKY,   false},
    [MOOD_EXCITED]      = {100, -12,  -5, MO_BIG_SMILE, IR_GREEN, true },
    [MOOD_LOVE]         = {100,  -8,  -3, MO_SMILE,     IR_PINK,  true },
    [MOOD_ANGRY]        = { 90,  20,   4, MO_FLAT,      IR_RED,   false},
    [MOOD_CONFUSED]     = {100, -10,  -4, MO_SMIRK,     IR_AMBER, false},
    [MOOD_THINKING]     = { 80,   8,   0, MO_NEUTRAL,   IR_AMBER, false},
    [MOOD_SLEEPY]       = { 40,   2,   6, MO_NEUTRAL,   IR_SKY,   false},
    [MOOD_SHY]          = {100,  -8,  -2, MO_SMILE,     IR_PINK,  true },
    [MOOD_PROUD]        = {100, -10,  -4, MO_SMIRK,     IR_BLUE,  false},
    [MOOD_WORRIED]      = { 90, -14,  -2, MO_FROWN,     IR_SKY,   false},
    [MOOD_PLAYFUL]      = {100, -12,  -4, MO_BIG_SMILE, IR_GREEN, true },
    [MOOD_CALM]         = { 88,   0,   0, MO_NEUTRAL,   IR_BLUE,  false},
    [MOOD_GRUMPY]       = { 70,  16,   5, MO_FROWN,     IR_RED,   false},
    [MOOD_HOPEFUL]      = {100,  -8,  -5, MO_SMILE,     IR_BLUE,  false},
    [MOOD_GRATEFUL]     = {100,  -8,  -3, MO_SMILE,     IR_GREEN, true },
    [MOOD_DISAPPOINTED] = { 88, -10,   0, MO_FROWN,     IR_SKY,   false},
    [MOOD_SILLY]        = {100, -12,  -5, MO_BIG_SMILE, IR_GREEN, true },
};

// ─── LVGL flush plumbing ──────────────────────────────────────────────────────
static bool _on_flush_ready(esp_lcd_panel_io_handle_t io,
                             esp_lcd_panel_io_event_data_t *edata, void *user_ctx) {
    lv_disp_flush_ready((lv_disp_drv_t *)user_ctx);
    return false;
}
static void _flush_cb(lv_disp_drv_t *drv, const lv_area_t *area, lv_color_t *map) {
    esp_lcd_panel_draw_bitmap(s_panel, area->x1, area->y1, area->x2 + 1, area->y2 + 1, map);
}
static void _tick_cb(void *arg) { lv_tick_inc(LVGL_TICK_PERIOD_MS); }

static void _lvgl_task(void *arg) {
    for (;;) {
        if (xSemaphoreTake(s_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            uint32_t ms = lv_timer_handler();
            xSemaphoreGive(s_mutex);
            vTaskDelay(pdMS_TO_TICKS(ms < 1 ? 1 : ms > 50 ? 50 : ms));
        } else {
            vTaskDelay(pdMS_TO_TICKS(10));
        }
    }
}

// ─── Small style helpers ──────────────────────────────────────────────────────
static void style_circle(lv_obj_t *o, int d, lv_color_t c) {
    lv_obj_set_size(o, d, d);
    lv_obj_set_style_radius(o, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(o, c, 0);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(o, 0, 0);
    lv_obj_set_style_pad_all(o, 0, 0);
    lv_obj_clear_flag(o, LV_OBJ_FLAG_SCROLLABLE);
}

// Clean solid iris — depth comes from the gloss highlights, not a gradient
// (gradients band badly in RGB565 and looked muddy).
static void iris_set_color(lv_obj_t *iris, uint32_t rgb) {
    lv_obj_set_style_bg_color(iris, lv_color_hex(rgb), 0);
    lv_obj_set_style_bg_grad_dir(iris, LV_GRAD_DIR_NONE, 0);
}

// ─── Eye blink / openness animation (height only → partial repaint) ───────────
static void eye_h_cb(void *obj, int32_t h) {
    lv_obj_t *e = (lv_obj_t *)obj;
    lv_obj_set_height(e, h);
    lv_obj_set_y(e, EYE_CY - h / 2);
}

static void anim_eye_h(lv_obj_t *e, int32_t from, int32_t to, uint16_t t, uint16_t playback) {
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, e);
    lv_anim_set_exec_cb(&a, eye_h_cb);
    lv_anim_set_values(&a, from, to);
    lv_anim_set_time(&a, t);
    lv_anim_set_playback_time(&a, playback);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_in_out);
    lv_anim_start(&a);
}

static void blink_timer_cb(lv_timer_t *t) {
    int h = s_eye_target_h;
    anim_eye_h(s_eye_l, h, 8, 90, 90);
    anim_eye_h(s_eye_r, h, 8, 90, 90);
    // schedule next blink at a natural, slightly random interval
    lv_timer_set_period(t, 2200 + (esp_random() % 2600));
}

// ─── Idle eye drift (glides the iris within the eye → alive, no twitch) ────────
static void set_x_cb(void *o, int32_t v) { lv_obj_set_x((lv_obj_t *)o, (lv_coord_t)v); }
static void set_y_cb(void *o, int32_t v) { lv_obj_set_y((lv_obj_t *)o, (lv_coord_t)v); }

static void anim_axis(lv_obj_t *o, lv_anim_exec_xcb_t cb, int32_t from, int32_t to, uint16_t t) {
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, o);
    lv_anim_set_exec_cb(&a, cb);
    lv_anim_set_values(&a, from, to);
    lv_anim_set_time(&a, t);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_in_out);
    lv_anim_start(&a);
}

static void drift_timer_cb(lv_timer_t *t) {
    // While a sound is being tracked, hold the gaze and skip random drift.
    if ((esp_timer_get_time() / 1000) < s_look_until_ms) {
        lv_timer_set_period(t, 150);
        return;
    }
    // Glance just ended → bring the eyes back to centre.
    if (lv_obj_get_x(s_eye_l) != s_eye_l_x0) {
        anim_axis(s_eye_l, set_x_cb, lv_obj_get_x(s_eye_l), s_eye_l_x0, 250);
        anim_axis(s_eye_r, set_x_cb, lv_obj_get_x(s_eye_r), s_eye_r_x0, 250);
    }
    int dx = 5 - (int)(esp_random() % 11);   // -5..+5
    int dy = 3 - (int)(esp_random() % 7);    // -3..+3
    int ix = (EYE_W - IRIS_D) / 2 + dx;
    int iy = (EYE_H - IRIS_D) / 2 + dy;
    anim_axis(s_iris_l, set_x_cb, lv_obj_get_x(s_iris_l), ix, 380);
    anim_axis(s_iris_l, set_y_cb, lv_obj_get_y(s_iris_l), iy, 380);
    anim_axis(s_iris_r, set_x_cb, lv_obj_get_x(s_iris_r), ix, 380);
    anim_axis(s_iris_r, set_y_cb, lv_obj_get_y(s_iris_r), iy, 380);
    lv_timer_set_period(t, 900 + (esp_random() % 1400));
}

// Glance toward a sound (called from the ears module, off the LVGL task).
void face_look(int pan) {
    if (pan < -100) pan = -100;
    else if (pan > 100) pan = 100;
    if (!s_mutex || xSemaphoreTake(s_mutex, pdMS_TO_TICKS(20)) != pdTRUE) return;

    int eoff = pan * 16 / 100;                       // whole-eye slide
    int ioff = pan * 6 / 100;                        // iris within the eye
    anim_axis(s_eye_l, set_x_cb, lv_obj_get_x(s_eye_l), s_eye_l_x0 + eoff, 160);
    anim_axis(s_eye_r, set_x_cb, lv_obj_get_x(s_eye_r), s_eye_r_x0 + eoff, 160);
    int ix = (EYE_W - IRIS_D) / 2 + ioff;
    anim_axis(s_iris_l, set_x_cb, lv_obj_get_x(s_iris_l), ix, 160);
    anim_axis(s_iris_r, set_x_cb, lv_obj_get_x(s_iris_r), ix, 160);

    s_look_until_ms = (esp_timer_get_time() / 1000) + 1000;
    xSemaphoreGive(s_mutex);
}

// ─── Apply a mood ─────────────────────────────────────────────────────────────
static void apply_look(sandy_mood_t mood) {
    if (mood >= MOOD_COUNT) mood = MOOD_IDLE;
    const look_t *l = &LOOKS[mood];

    s_eye_target_h = EYE_H * l->openness / 100;
    anim_eye_h(s_eye_l, lv_obj_get_height(s_eye_l), s_eye_target_h, 220, 0);
    anim_eye_h(s_eye_r, lv_obj_get_height(s_eye_r), s_eye_target_h, 220, 0);

    iris_set_color(s_iris_l, l->iris);
    iris_set_color(s_iris_r, l->iris);

    // Brows (every mood): raise/lower the whole brow + tilt the inner end.
    int by = BROW_BASE_Y + l->brow_dy;
    lv_obj_set_y(s_brow_l, by);
    lv_obj_set_y(s_brow_r, by);
    lv_obj_set_style_transform_angle(s_brow_l,  l->brow_deg * 10, 0);
    lv_obj_set_style_transform_angle(s_brow_r, -l->brow_deg * 10, 0);

    // Mouth: show the right shape, hide the others.
    lv_obj_add_flag(s_mouth_arc, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(s_mouth_bar, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(s_mouth_o,   LV_OBJ_FLAG_HIDDEN);
    switch (l->mouth) {
    case MO_SMILE:
        lv_obj_clear_flag(s_mouth_arc, LV_OBJ_FLAG_HIDDEN);
        lv_arc_set_angles(s_mouth_arc, 30, 150);          // bottom arc = smile
        lv_obj_align(s_mouth_arc, LV_ALIGN_CENTER, 0, 48); // curve lands at the mouth line
        break;
    case MO_BIG_SMILE:
        lv_obj_clear_flag(s_mouth_arc, LV_OBJ_FLAG_HIDDEN);
        lv_arc_set_angles(s_mouth_arc, 15, 165);
        lv_obj_align(s_mouth_arc, LV_ALIGN_CENTER, 0, 46);
        break;
    case MO_FROWN:
        lv_obj_clear_flag(s_mouth_arc, LV_OBJ_FLAG_HIDDEN);
        lv_arc_set_angles(s_mouth_arc, 210, 330);          // top arc = frown
        lv_obj_align(s_mouth_arc, LV_ALIGN_CENTER, 0, 132); // sits low so the curve is at the mouth
        break;
    case MO_OPEN:
        lv_obj_clear_flag(s_mouth_o, LV_OBJ_FLAG_HIDDEN);
        break;
    case MO_SMIRK:
        lv_obj_clear_flag(s_mouth_bar, LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_style_transform_angle(s_mouth_bar, 120, 0);   // 12° tilt
        break;
    case MO_FLAT:
        lv_obj_clear_flag(s_mouth_bar, LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_style_transform_angle(s_mouth_bar, 0, 0);
        lv_obj_set_width(s_mouth_bar, 60);
        break;
    case MO_NEUTRAL:
    default:
        lv_obj_clear_flag(s_mouth_bar, LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_style_transform_angle(s_mouth_bar, 0, 0);
        lv_obj_set_width(s_mouth_bar, 46);
        break;
    }

    if (l->blush) {
        lv_obj_clear_flag(s_blush_l, LV_OBJ_FLAG_HIDDEN);
        lv_obj_clear_flag(s_blush_r, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(s_blush_l, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_blush_r, LV_OBJ_FLAG_HIDDEN);
    }
}

static void mood_timer_cb(lv_timer_t *t) {
    static sandy_mood_t last = 0xFF;
    if (s_mood != last) {
        last = s_mood;
        apply_look(last);
    }
}

// Ignored for FACE_SLEEP_AFTER_MS while idle → doze off. Anything expressive
// (wake word, proximity, a cloud mood) resets the clock in face_set_mood and
// snaps her awake by simply setting a new mood.
static void sleep_timer_cb(lv_timer_t *t) {
    if (s_mood != MOOD_IDLE) return;
    int64_t now = esp_timer_get_time() / 1000;
    if (s_last_active_ms == 0) s_last_active_ms = now;   // boot counts as activity
    if (now - s_last_active_ms > FACE_SLEEP_AFTER_MS) {
        s_mood = MOOD_SLEEPY;
        g_current_mood = MOOD_SLEEPY;
        ESP_LOGI(TAG, "dozing off (idle %d min)", (int)((now - s_last_active_ms) / 60000));
    }
}

// ─── Demo: cycle every mood so we can eyeball them on hardware ────────────────
#define FACE_DEMO 0
#if FACE_DEMO
static const char *MOOD_NAMES[MOOD_COUNT] = {
    "IDLE", "HAPPY", "CURIOUS", "SAD", "ALERT", "SURPRISED", "BIG_HAPPY",
    "FOCUSED", "BORED", "EXCITED", "LOVE", "ANGRY", "CONFUSED", "THINKING",
    "SLEEPY", "SHY", "PROUD", "WORRIED", "PLAYFUL", "CALM", "GRUMPY",
    "HOPEFUL", "GRATEFUL", "DISAPPOINTED", "SILLY",
};
static void demo_timer_cb(lv_timer_t *t) {
    sandy_mood_t m = (s_mood + 1) % MOOD_COUNT;
    s_mood = m;
    g_current_mood = m;
    ESP_LOGI(TAG, "demo mood [%d/%d]: %s", (int)m + 1, MOOD_COUNT, MOOD_NAMES[m]);
}
#endif

// ─── Build the face objects ───────────────────────────────────────────────────
static lv_obj_t *make_eye(int x) {
    lv_obj_t *eye = lv_obj_create(lv_scr_act());
    lv_obj_set_size(eye, EYE_W, EYE_H);
    lv_obj_set_pos(eye, x, EYE_CY - EYE_H / 2);
    lv_obj_set_style_radius(eye, LV_RADIUS_CIRCLE, 0);   // round eye
    lv_obj_set_style_bg_color(eye, C_SCLERA, 0);         // thin white rim behind the iris
    lv_obj_set_style_border_width(eye, 0, 0);
    lv_obj_set_style_pad_all(eye, 0, 0);
    lv_obj_clear_flag(eye, LV_OBJ_FLAG_SCROLLABLE);
    return eye;
}

static void build_face(void) {
    lv_obj_set_style_bg_color(lv_scr_act(), C_BG, 0);
    lv_obj_clear_flag(lv_scr_act(), LV_OBJ_FLAG_SCROLLABLE);

    int cx = TFT_WIDTH / 2;

    // Eyes
    s_eye_l = make_eye(cx - EYE_DX - EYE_W / 2);
    s_eye_r = make_eye(cx + EYE_DX - EYE_W / 2);
    s_eye_l_x0 = cx - EYE_DX - EYE_W / 2;
    s_eye_r_x0 = cx + EYE_DX - EYE_W / 2;

    // Iris (child of each eye) carries pupil + gloss and moves for "look-around"
    int ipos = (EYE_W - IRIS_D) / 2;
    s_iris_l = lv_obj_create(s_eye_l);
    style_circle(s_iris_l, IRIS_D, lv_color_hex(IR_BLUE));
    lv_obj_set_pos(s_iris_l, ipos, ipos);
    s_iris_r = lv_obj_create(s_eye_r);
    style_circle(s_iris_r, IRIS_D, lv_color_hex(IR_BLUE));
    lv_obj_set_pos(s_iris_r, ipos, ipos);
    iris_set_color(s_iris_l, IR_BLUE);
    iris_set_color(s_iris_r, IR_BLUE);

    for (int i = 0; i < 2; i++) {
        lv_obj_t *iris = i ? s_iris_r : s_iris_l;
        lv_obj_t *pupil = lv_obj_create(iris);
        style_circle(pupil, PUPIL_D, C_PUPIL);
        lv_obj_center(pupil);
        lv_obj_t *gloss = lv_obj_create(iris);
        style_circle(gloss, GLOSS_D, C_GLOSS);
        lv_obj_align(gloss, LV_ALIGN_TOP_LEFT, 9, 7);
        lv_obj_t *gloss2 = lv_obj_create(iris);     // small secondary reflection
        style_circle(gloss2, 7, C_GLOSS);
        lv_obj_align(gloss2, LV_ALIGN_BOTTOM_RIGHT, -12, -12);
    }

    // Brows
    int by = EYE_CY - EYE_H / 2 - 22;
    s_brow_l = lv_obj_create(lv_scr_act());
    lv_obj_set_size(s_brow_l, BROW_W, BROW_H);
    lv_obj_set_pos(s_brow_l, cx - EYE_DX - BROW_W / 2, by);
    lv_obj_set_style_radius(s_brow_l, BROW_H / 2, 0);
    lv_obj_set_style_bg_color(s_brow_l, C_WHITE, 0);
    lv_obj_set_style_border_width(s_brow_l, 0, 0);
    lv_obj_set_style_transform_pivot_x(s_brow_l, BROW_W / 2, 0);
    lv_obj_set_style_transform_pivot_y(s_brow_l, BROW_H / 2, 0);
    lv_obj_clear_flag(s_brow_l, LV_OBJ_FLAG_SCROLLABLE);

    s_brow_r = lv_obj_create(lv_scr_act());
    lv_obj_set_size(s_brow_r, BROW_W, BROW_H);
    lv_obj_set_pos(s_brow_r, cx + EYE_DX - BROW_W / 2, by);
    lv_obj_set_style_radius(s_brow_r, BROW_H / 2, 0);
    lv_obj_set_style_bg_color(s_brow_r, C_WHITE, 0);
    lv_obj_set_style_border_width(s_brow_r, 0, 0);
    lv_obj_set_style_transform_pivot_x(s_brow_r, BROW_W / 2, 0);
    lv_obj_set_style_transform_pivot_y(s_brow_r, BROW_H / 2, 0);
    lv_obj_clear_flag(s_brow_r, LV_OBJ_FLAG_SCROLLABLE);

    // Mouth — arc (smile/frown)
    s_mouth_arc = lv_arc_create(lv_scr_act());
    lv_obj_set_size(s_mouth_arc, 96, 96);
    lv_obj_align(s_mouth_arc, LV_ALIGN_CENTER, 0, 80);
    lv_arc_set_bg_angles(s_mouth_arc, 0, 0);
    lv_arc_set_angles(s_mouth_arc, 30, 150);
    lv_obj_set_style_arc_color(s_mouth_arc, C_WHITE, LV_PART_INDICATOR);
    lv_obj_set_style_arc_width(s_mouth_arc, 12, LV_PART_INDICATOR);
    lv_obj_set_style_arc_rounded(s_mouth_arc, true, LV_PART_INDICATOR);
    lv_obj_remove_style(s_mouth_arc, NULL, LV_PART_KNOB);
    lv_obj_clear_flag(s_mouth_arc, LV_OBJ_FLAG_CLICKABLE);

    // Mouth — bar (neutral/flat)
    s_mouth_bar = lv_obj_create(lv_scr_act());
    lv_obj_set_size(s_mouth_bar, 46, 14);
    lv_obj_align(s_mouth_bar, LV_ALIGN_CENTER, 0, 90);
    lv_obj_set_style_radius(s_mouth_bar, 7, 0);
    lv_obj_set_style_bg_color(s_mouth_bar, C_WHITE, 0);
    lv_obj_set_style_border_width(s_mouth_bar, 0, 0);
    lv_obj_set_style_transform_pivot_x(s_mouth_bar, 23, 0);
    lv_obj_set_style_transform_pivot_y(s_mouth_bar, 7, 0);
    lv_obj_clear_flag(s_mouth_bar, LV_OBJ_FLAG_SCROLLABLE);

    // Mouth — open circle (surprised)
    s_mouth_o = lv_obj_create(lv_scr_act());
    style_circle(s_mouth_o, 34, C_WHITE);
    lv_obj_align(s_mouth_o, LV_ALIGN_CENTER, 0, 90);
    lv_obj_t *o_in = lv_obj_create(s_mouth_o);
    style_circle(o_in, 20, C_BG);
    lv_obj_center(o_in);

    // Blush
    s_blush_l = lv_obj_create(lv_scr_act());
    style_circle(s_blush_l, 18, C_BLUSH);
    lv_obj_align(s_blush_l, LV_ALIGN_CENTER, -82, 40);
    s_blush_r = lv_obj_create(lv_scr_act());
    style_circle(s_blush_r, 18, C_BLUSH);
    lv_obj_align(s_blush_r, LV_ALIGN_CENTER, 82, 40);

    apply_look(MOOD_IDLE);

    // Animations: periodic blink + idle eye drift + dozing off when ignored.
    lv_timer_create(blink_timer_cb, 3000, NULL);
    lv_timer_create(drift_timer_cb, 600, NULL);
    lv_timer_create(mood_timer_cb, 60, NULL);
    lv_timer_create(sleep_timer_cb, 10000, NULL);
#if FACE_DEMO
    lv_timer_create(demo_timer_cb, 5000, NULL);   // cycle all moods, 5 s each
#endif
}

// ─── LCD hardware init ────────────────────────────────────────────────────────
static esp_err_t _lcd_init(void) {
    ledc_timer_config_t bl_timer = {
        .speed_mode = LEDC_LOW_SPEED_MODE, .duty_resolution = LEDC_TIMER_8_BIT,
        .timer_num = LEDC_TIMER_2, .freq_hz = 5000, .clk_cfg = LEDC_AUTO_CLK,
    };
    ledc_timer_config(&bl_timer);
    ledc_channel_config_t bl_ch = {
        .gpio_num = PIN_TFT_BLK, .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel = LEDC_CHANNEL_2, .timer_sel = LEDC_TIMER_2, .duty = 200, .hpoint = 0,
    };
    ledc_channel_config(&bl_ch);

    spi_bus_config_t bus = {
        .mosi_io_num = PIN_TFT_MOSI, .miso_io_num = -1, .sclk_io_num = PIN_TFT_SCLK,
        .quadwp_io_num = -1, .quadhd_io_num = -1, .max_transfer_sz = TFT_WIDTH * LCD_BUF_LINES * 2,
    };
    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO));

    esp_lcd_panel_io_handle_t io;
    esp_lcd_panel_io_spi_config_t io_cfg = {
        .dc_gpio_num = PIN_TFT_DC, .cs_gpio_num = PIN_TFT_CS,
        .pclk_hz = 40 * 1000 * 1000, .lcd_cmd_bits = 8, .lcd_param_bits = 8,
        .spi_mode = 0, .trans_queue_depth = 10,
        .on_color_trans_done = _on_flush_ready, .user_ctx = &s_drv,
    };
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI2_HOST, &io_cfg, &io));

    esp_lcd_panel_dev_config_t panel_cfg = {
        .reset_gpio_num = PIN_TFT_RST, .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB, .bits_per_pixel = 16,
    };
    ESP_ERROR_CHECK(esp_lcd_new_panel_st7789(io, &panel_cfg, &s_panel));
    ESP_ERROR_CHECK(esp_lcd_panel_reset(s_panel));
    ESP_ERROR_CHECK(esp_lcd_panel_init(s_panel));
    ESP_ERROR_CHECK(esp_lcd_panel_invert_color(s_panel, true));
    ESP_ERROR_CHECK(esp_lcd_panel_set_gap(s_panel, 0, 0));
    ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(s_panel, true));
    return ESP_OK;
}

static void _lvgl_init(void) {
    lv_init();
    lv_disp_draw_buf_init(&s_draw_buf, s_buf1, s_buf2, TFT_WIDTH * LCD_BUF_LINES);
    lv_disp_drv_init(&s_drv);
    s_drv.hor_res = TFT_WIDTH;
    s_drv.ver_res = TFT_HEIGHT;
    s_drv.flush_cb = _flush_cb;
    s_drv.draw_buf = &s_draw_buf;
    s_drv.user_data = &s_drv;
    s_disp = lv_disp_drv_register(&s_drv);

    const esp_timer_create_args_t tick_args = { .callback = _tick_cb, .name = "lvgl_tick" };
    esp_timer_handle_t tick_timer;
    ESP_ERROR_CHECK(esp_timer_create(&tick_args, &tick_timer));
    ESP_ERROR_CHECK(esp_timer_start_periodic(tick_timer, LVGL_TICK_PERIOD_MS * 1000));
}

// ─── Public API ───────────────────────────────────────────────────────────────
esp_err_t face_init(void) {
    s_mutex = xSemaphoreCreateMutex();
    ESP_ERROR_CHECK(_lcd_init());
    _lvgl_init();
    if (xSemaphoreTake(s_mutex, portMAX_DELAY)) {
        build_face();
        xSemaphoreGive(s_mutex);
    }
    xTaskCreatePinnedToCore(_lvgl_task, "lvgl", LVGL_TASK_STACK, NULL, LVGL_TASK_PRIORITY, NULL, 1);
    ESP_LOGI(TAG, "ready — ST7789, LVGL object face");
    return ESP_OK;
}

void face_set_mood(sandy_mood_t mood) {
    if (mood < MOOD_COUNT) {
        s_mood = mood;
        g_current_mood = mood;
        // Any expressed mood counts as interaction and resets the sleep clock
        // (idle/sleepy don't, or she could never doze off).
        if (mood != MOOD_IDLE && mood != MOOD_SLEEPY) {
            s_last_active_ms = esp_timer_get_time() / 1000;
        }
    }
}
