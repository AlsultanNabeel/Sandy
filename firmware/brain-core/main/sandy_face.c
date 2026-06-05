#include "sandy_face.h"
#include "config.h"
#include "esp_log.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_lcd_panel_ops.h"
#include "driver/spi_master.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lvgl.h"
#include <math.h>

static const char *TAG = "face";

// ─── Display + LVGL internals ─────────────────────────────────────────────────
#define LVGL_TICK_PERIOD_MS     2
#define LVGL_TASK_STACK         6144
#define LVGL_TASK_PRIORITY      5
#define LCD_BUF_LINES           40      // DMA buffer: 40 lines × 240 px × 2 bytes

static esp_lcd_panel_handle_t s_panel   = NULL;
static lv_disp_t             *s_disp   = NULL;
static lv_disp_draw_buf_t     s_draw_buf;
static lv_color_t              s_buf1[TFT_WIDTH * LCD_BUF_LINES];
static lv_color_t              s_buf2[TFT_WIDTH * LCD_BUF_LINES];
static SemaphoreHandle_t       s_mutex;     // guards LVGL calls from face_set_mood

// ─── Face widgets ─────────────────────────────────────────────────────────────
static lv_obj_t *s_bg;
static lv_obj_t *s_eye_l, *s_eye_r;
static lv_obj_t *s_pupil_l, *s_pupil_r;
static lv_obj_t *s_brow_l, *s_brow_r;
static lv_obj_t *s_mouth;

// Brow line points (two points each)
static lv_point_t s_brow_l_pts[2], s_brow_r_pts[2];

// ─── Colour palette ───────────────────────────────────────────────────────────
#define C_BG_DARK    lv_color_hex(0x0A0A1A)
#define C_EYE        lv_color_hex(0xFFFFFF)
#define C_PUPIL      lv_color_hex(0x1A1AFF)
#define C_BROW       lv_color_hex(0xFFFFFF)
#define C_MOUTH      lv_color_hex(0xFFFFFF)

// ─── Mood descriptor ──────────────────────────────────────────────────────────
typedef struct {
    lv_color_t  bg;
    int16_t     eye_h;          // eye height (squint effect)
    int16_t     eye_w;
    int16_t     brow_angle;     // degrees: + = inner up (angry), - = inner down (sad)
    int16_t     mouth_start;    // arc start angle (0=right, 90=down)
    int16_t     mouth_end;      // arc end angle
    bool        mouth_frown;    // true = frown (flip arc)
} mood_desc_t;

// Mouth angles: LVGL arcs go clockwise. 90°=bottom, 270°=top.
// Smile: 210→330 (bottom half, ~120° arc). Frown: flip to 30→150 (top half).
static const mood_desc_t MOODS[MOOD_COUNT] = {
    [MOOD_IDLE]        = {.bg=lv_color_hex(0x0A0A1A),.eye_h=26,.eye_w=26,.brow_angle= 0,.mouth_start=210,.mouth_end=330,.mouth_frown=false},
    [MOOD_HAPPY]       = {.bg=lv_color_hex(0x001A00),.eye_h=24,.eye_w=30,.brow_angle= 5,.mouth_start=200,.mouth_end=340,.mouth_frown=false},
    [MOOD_BIG_HAPPY]   = {.bg=lv_color_hex(0x001A00),.eye_h=20,.eye_w=34,.brow_angle=10,.mouth_start=190,.mouth_end=350,.mouth_frown=false},
    [MOOD_CURIOUS]     = {.bg=lv_color_hex(0x0A0A20),.eye_h=30,.eye_w=26,.brow_angle= 8,.mouth_start=215,.mouth_end=325,.mouth_frown=false},
    [MOOD_SAD]         = {.bg=lv_color_hex(0x0A0010),.eye_h=22,.eye_w=24,.brow_angle=-8,.mouth_start= 30,.mouth_end=150,.mouth_frown=true },
    [MOOD_ALERT]       = {.bg=lv_color_hex(0x1A0500),.eye_h=32,.eye_w=32,.brow_angle= 2,.mouth_start=205,.mouth_end=335,.mouth_frown=false},
    [MOOD_SURPRISED]   = {.bg=lv_color_hex(0x1A1000),.eye_h=36,.eye_w=36,.brow_angle=12,.mouth_start=205,.mouth_end=335,.mouth_frown=false},
    [MOOD_FOCUSED]     = {.bg=lv_color_hex(0x001010),.eye_h=18,.eye_w=28,.brow_angle= 3,.mouth_start=215,.mouth_end=325,.mouth_frown=false},
    [MOOD_BORED]       = {.bg=lv_color_hex(0x0A0A0A),.eye_h=14,.eye_w=28,.brow_angle=-3,.mouth_start=215,.mouth_end=325,.mouth_frown=false},
    [MOOD_EXCITED]     = {.bg=lv_color_hex(0x10001A),.eye_h=30,.eye_w=32,.brow_angle=10,.mouth_start=195,.mouth_end=345,.mouth_frown=false},
    [MOOD_LOVE]        = {.bg=lv_color_hex(0x1A0010),.eye_h=20,.eye_w=28,.brow_angle= 5,.mouth_start=200,.mouth_end=340,.mouth_frown=false},
    [MOOD_ANGRY]       = {.bg=lv_color_hex(0x1A0000),.eye_h=18,.eye_w=26,.brow_angle=15,.mouth_start= 40,.mouth_end=140,.mouth_frown=true },
    [MOOD_CONFUSED]    = {.bg=lv_color_hex(0x0A0A1A),.eye_h=26,.eye_w=26,.brow_angle=-5,.mouth_start=210,.mouth_end=330,.mouth_frown=false},
    [MOOD_THINKING]    = {.bg=lv_color_hex(0x001010),.eye_h=20,.eye_w=24,.brow_angle= 6,.mouth_start=215,.mouth_end=315,.mouth_frown=false},
    [MOOD_SLEEPY]      = {.bg=lv_color_hex(0x050510),.eye_h=10,.eye_w=28,.brow_angle=-2,.mouth_start=215,.mouth_end=325,.mouth_frown=false},
    [MOOD_SHY]         = {.bg=lv_color_hex(0x1A0A0A),.eye_h=22,.eye_w=24,.brow_angle= 4,.mouth_start=215,.mouth_end=325,.mouth_frown=false},
    [MOOD_PROUD]       = {.bg=lv_color_hex(0x000F1A),.eye_h=22,.eye_w=28,.brow_angle= 6,.mouth_start=205,.mouth_end=335,.mouth_frown=false},
    [MOOD_WORRIED]     = {.bg=lv_color_hex(0x0A0A00),.eye_h=24,.eye_w=24,.brow_angle=-6,.mouth_start= 40,.mouth_end=140,.mouth_frown=true },
    [MOOD_PLAYFUL]     = {.bg=lv_color_hex(0x001A10),.eye_h=24,.eye_w=30,.brow_angle= 8,.mouth_start=200,.mouth_end=340,.mouth_frown=false},
    [MOOD_CALM]        = {.bg=lv_color_hex(0x001010),.eye_h=22,.eye_w=26,.brow_angle= 2,.mouth_start=215,.mouth_end=325,.mouth_frown=false},
    [MOOD_GRUMPY]      = {.bg=lv_color_hex(0x0F0500),.eye_h=16,.eye_w=26,.brow_angle=12,.mouth_start= 35,.mouth_end=145,.mouth_frown=true },
    [MOOD_HOPEFUL]     = {.bg=lv_color_hex(0x001510),.eye_h=28,.eye_w=28,.brow_angle= 4,.mouth_start=205,.mouth_end=335,.mouth_frown=false},
    [MOOD_GRATEFUL]    = {.bg=lv_color_hex(0x001000),.eye_h=22,.eye_w=28,.brow_angle= 5,.mouth_start=200,.mouth_end=340,.mouth_frown=false},
    [MOOD_DISAPPOINTED]= {.bg=lv_color_hex(0x0A0A10),.eye_h=20,.eye_w=24,.brow_angle=-7,.mouth_start= 35,.mouth_end=145,.mouth_frown=true },
    [MOOD_SILLY]       = {.bg=lv_color_hex(0x100A00),.eye_h=28,.eye_w=32,.brow_angle=10,.mouth_start=190,.mouth_end=350,.mouth_frown=false},
};

// ─── LVGL flush callback ──────────────────────────────────────────────────────
static bool _on_flush_ready(esp_lcd_panel_io_handle_t io,
                             esp_lcd_panel_io_event_data_t *edata, void *user_ctx) {
    lv_disp_drv_t *drv = (lv_disp_drv_t *)user_ctx;
    lv_disp_flush_ready(drv);
    return false;
}

static void _flush_cb(lv_disp_drv_t *drv, const lv_area_t *area, lv_color_t *map) {
    esp_lcd_panel_draw_bitmap(s_panel,
        area->x1, area->y1, area->x2 + 1, area->y2 + 1, map);
}

// ─── LVGL tick + task ─────────────────────────────────────────────────────────
static void _tick_cb(void *arg) {
    lv_tick_inc(LVGL_TICK_PERIOD_MS);
}

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

// ─── Blink animation ──────────────────────────────────────────────────────────
static void _blink_anim_cb(void *obj, int32_t v) {
    lv_obj_set_height((lv_obj_t *)obj, v);
}

static void _start_blink(void) {
    int32_t h = lv_obj_get_height(s_eye_l);
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_exec_cb(&a, _blink_anim_cb);
    lv_anim_set_time(&a, 80);
    lv_anim_set_playback_time(&a, 80);
    lv_anim_set_values(&a, h, 2);
    lv_anim_set_var(&a, s_eye_l);
    lv_anim_start(&a);
    lv_anim_set_var(&a, s_eye_r);
    lv_anim_start(&a);
}

// ─── Apply mood to widgets ────────────────────────────────────────────────────
static void _apply_mood(sandy_mood_t mood) {
    if (mood >= MOOD_COUNT) mood = MOOD_IDLE;
    const mood_desc_t *d = &MOODS[mood];

    // Blink before changing expression (hides transition)
    _start_blink();
    vTaskDelay(pdMS_TO_TICKS(100));

    // Background colour
    lv_obj_set_style_bg_color(s_bg, d->bg, 0);

    // Eyes (ellipses centered at 80,120 and 160,120)
    lv_obj_set_size(s_eye_l, d->eye_w, d->eye_h);
    lv_obj_set_size(s_eye_r, d->eye_w, d->eye_h);

    // Pupils (fixed 10×10, follow eye centre)
    lv_obj_align(s_pupil_l, LV_ALIGN_CENTER, 0, 4);
    lv_obj_align(s_pupil_r, LV_ALIGN_CENTER, 0, 4);

    // Eyebrows — tilt via endpoint offset
    int16_t off = (int16_t)(d->brow_angle * 0.4f);
    s_brow_l_pts[0] = (lv_point_t){55,  85 + off};
    s_brow_l_pts[1] = (lv_point_t){105, 85 - off};
    s_brow_r_pts[0] = (lv_point_t){135, 85 - off};
    s_brow_r_pts[1] = (lv_point_t){185, 85 + off};
    lv_line_set_points(s_brow_l, s_brow_l_pts, 2);
    lv_line_set_points(s_brow_r, s_brow_r_pts, 2);

    // Mouth arc
    lv_arc_set_angles(s_mouth, d->mouth_start, d->mouth_end);
    if (d->mouth_frown) {
        lv_obj_set_style_arc_color(s_mouth, lv_color_hex(0xAAAAAA), LV_PART_INDICATOR);
    } else {
        lv_obj_set_style_arc_color(s_mouth, C_MOUTH, LV_PART_INDICATOR);
    }
}

// ─── Build initial face widgets ───────────────────────────────────────────────
static void _build_face(void) {
    // Background
    s_bg = lv_obj_create(lv_scr_act());
    lv_obj_set_size(s_bg, TFT_WIDTH, TFT_HEIGHT);
    lv_obj_set_pos(s_bg, 0, 0);
    lv_obj_set_style_bg_color(s_bg, C_BG_DARK, 0);
    lv_obj_set_style_border_width(s_bg, 0, 0);
    lv_obj_set_style_pad_all(s_bg, 0, 0);

    // Left eye (centred at 80,120)
    s_eye_l = lv_obj_create(s_bg);
    lv_obj_set_size(s_eye_l, 26, 26);
    lv_obj_align(s_eye_l, LV_ALIGN_LEFT_MID, 18, 0);
    lv_obj_set_style_bg_color(s_eye_l, C_EYE, 0);
    lv_obj_set_style_radius(s_eye_l, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_border_width(s_eye_l, 0, 0);

    // Right eye
    s_eye_r = lv_obj_create(s_bg);
    lv_obj_set_size(s_eye_r, 26, 26);
    lv_obj_align(s_eye_r, LV_ALIGN_RIGHT_MID, -18, 0);
    lv_obj_set_style_bg_color(s_eye_r, C_EYE, 0);
    lv_obj_set_style_radius(s_eye_r, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_border_width(s_eye_r, 0, 0);

    // Pupils
    s_pupil_l = lv_obj_create(s_eye_l);
    lv_obj_set_size(s_pupil_l, 10, 10);
    lv_obj_align(s_pupil_l, LV_ALIGN_CENTER, 0, 4);
    lv_obj_set_style_bg_color(s_pupil_l, C_PUPIL, 0);
    lv_obj_set_style_radius(s_pupil_l, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_border_width(s_pupil_l, 0, 0);

    s_pupil_r = lv_obj_create(s_eye_r);
    lv_obj_set_size(s_pupil_r, 10, 10);
    lv_obj_align(s_pupil_r, LV_ALIGN_CENTER, 0, 4);
    lv_obj_set_style_bg_color(s_pupil_r, C_PUPIL, 0);
    lv_obj_set_style_radius(s_pupil_r, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_border_width(s_pupil_r, 0, 0);

    // Eyebrows (lines)
    s_brow_l_pts[0] = (lv_point_t){55, 85};
    s_brow_l_pts[1] = (lv_point_t){105,85};
    s_brow_l = lv_line_create(s_bg);
    lv_line_set_points(s_brow_l, s_brow_l_pts, 2);
    lv_obj_set_style_line_color(s_brow_l, C_BROW, 0);
    lv_obj_set_style_line_width(s_brow_l, 4, 0);
    lv_obj_set_style_line_rounded(s_brow_l, true, 0);

    s_brow_r_pts[0] = (lv_point_t){135,85};
    s_brow_r_pts[1] = (lv_point_t){185,85};
    s_brow_r = lv_line_create(s_bg);
    lv_line_set_points(s_brow_r, s_brow_r_pts, 2);
    lv_obj_set_style_line_color(s_brow_r, C_BROW, 0);
    lv_obj_set_style_line_width(s_brow_r, 4, 0);
    lv_obj_set_style_line_rounded(s_brow_r, true, 0);

    // Mouth arc (centred bottom half, radius 40)
    s_mouth = lv_arc_create(s_bg);
    lv_obj_set_size(s_mouth, 80, 80);
    lv_obj_align(s_mouth, LV_ALIGN_BOTTOM_MID, 0, -20);
    lv_arc_set_angles(s_mouth, 210, 330);
    lv_arc_set_bg_angles(s_mouth, 0, 0);        // hide background track
    lv_obj_set_style_arc_color(s_mouth, C_MOUTH, LV_PART_INDICATOR);
    lv_obj_set_style_arc_width(s_mouth, 5, LV_PART_INDICATOR);
    lv_obj_remove_style(s_mouth, NULL, LV_PART_KNOB);
    lv_obj_clear_flag(s_mouth, LV_OBJ_FLAG_CLICKABLE);

    ESP_LOGI(TAG, "face widgets created");
}

// ─── LCD hardware init ────────────────────────────────────────────────────────
static esp_err_t _lcd_init(void) {
    // Backlight
    ledc_timer_config_t bl_timer = {
        .speed_mode      = LEDC_LOW_SPEED_MODE,
        .duty_resolution = LEDC_TIMER_8_BIT,
        .timer_num       = LEDC_TIMER_2,
        .freq_hz         = 5000,
        .clk_cfg         = LEDC_AUTO_CLK,
    };
    ledc_timer_config(&bl_timer);
    ledc_channel_config_t bl_ch = {
        .gpio_num   = PIN_TFT_BLK,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel    = LEDC_CHANNEL_2,
        .timer_sel  = LEDC_TIMER_2,
        .duty       = 200,  // ~78% brightness
        .hpoint     = 0,
    };
    ledc_channel_config(&bl_ch);

    // SPI bus
    spi_bus_config_t bus = {
        .mosi_io_num     = PIN_TFT_MOSI,
        .miso_io_num     = -1,
        .sclk_io_num     = PIN_TFT_SCLK,
        .quadwp_io_num   = -1,
        .quadhd_io_num   = -1,
        .max_transfer_sz = TFT_WIDTH * LCD_BUF_LINES * 2,
    };
    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO));

    // LCD IO (SPI panel)
    esp_lcd_panel_io_handle_t io;
    esp_lcd_panel_io_spi_config_t io_cfg = {
        .dc_gpio_num       = PIN_TFT_DC,
        .cs_gpio_num       = PIN_TFT_CS,
        .pclk_hz           = 40 * 1000 * 1000,   // 40 MHz
        .lcd_cmd_bits      = 8,
        .lcd_param_bits    = 8,
        .spi_mode          = 0,
        .trans_queue_depth = 10,
        .on_color_trans_done = _on_flush_ready,
    };
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi(
        (esp_lcd_spi_bus_handle_t)SPI2_HOST, &io_cfg, &io));

    // ST7789 panel
    esp_lcd_panel_dev_config_t panel_cfg = {
        .reset_gpio_num = PIN_TFT_RST,
        .rgb_endian     = LCD_RGB_ENDIAN_RGB,
        .bits_per_pixel = 16,
    };
    ESP_ERROR_CHECK(esp_lcd_new_panel_st7789(io, &panel_cfg, &s_panel));
    ESP_ERROR_CHECK(esp_lcd_panel_reset(s_panel));
    ESP_ERROR_CHECK(esp_lcd_panel_init(s_panel));
    ESP_ERROR_CHECK(esp_lcd_panel_invert_color(s_panel, true));  // ST7789 needs invert
    ESP_ERROR_CHECK(esp_lcd_panel_set_gap(s_panel, 0, 0));
    ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(s_panel, true));

    return ESP_OK;
}

// ─── LVGL display driver init ─────────────────────────────────────────────────
static void _lvgl_init(void) {
    lv_init();
    lv_disp_draw_buf_init(&s_draw_buf, s_buf1, s_buf2, TFT_WIDTH * LCD_BUF_LINES);

    static lv_disp_drv_t drv;
    lv_disp_drv_init(&drv);
    drv.hor_res    = TFT_WIDTH;
    drv.ver_res    = TFT_HEIGHT;
    drv.flush_cb   = _flush_cb;
    drv.draw_buf   = &s_draw_buf;
    drv.user_data  = &drv;
    s_disp = lv_disp_drv_register(&drv);

    // LVGL tick via esp_timer
    const esp_timer_create_args_t tick_args = {
        .callback = _tick_cb,
        .name     = "lvgl_tick",
    };
    esp_timer_handle_t tick_timer;
    ESP_ERROR_CHECK(esp_timer_create(&tick_args, &tick_timer));
    ESP_ERROR_CHECK(esp_timer_start_periodic(tick_timer,
                                              LVGL_TICK_PERIOD_MS * 1000));
}

// ─── Public API ───────────────────────────────────────────────────────────────
esp_err_t face_init(void) {
    s_mutex = xSemaphoreCreateMutex();

    ESP_ERROR_CHECK(_lcd_init());
    _lvgl_init();

    if (xSemaphoreTake(s_mutex, portMAX_DELAY)) {
        _build_face();
        _apply_mood(MOOD_IDLE);
        xSemaphoreGive(s_mutex);
    }

    xTaskCreatePinnedToCore(_lvgl_task, "lvgl", LVGL_TASK_STACK,
                             NULL, LVGL_TASK_PRIORITY, NULL, 1);
    ESP_LOGI(TAG, "ready — ST7789 240×240 LVGL");
    return ESP_OK;
}

void face_set_mood(sandy_mood_t mood) {
    if (xSemaphoreTake(s_mutex, pdMS_TO_TICKS(100)) == pdTRUE) {
        _apply_mood(mood);
        xSemaphoreGive(s_mutex);
    }
}
