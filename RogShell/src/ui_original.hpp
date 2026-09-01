#pragma once
#include <iostream>
#include <windows.h>
#include <lmcons.h>
#include <string>
#include <thread>
#include <chrono>
#include <vector>
#include <cstdio>

namespace UI {

    // ── ANSI color codes ─────────────────────────────────────────────────────
    // 'inline' allows these to be defined in a header included by multiple .cpp files
    inline const char* CLAUDE_ORANGE = "\033[38;5;208m";
    inline const char* RESET         = "\033[0m";
    inline const char* GREEN         = "\033[1;32m";
    inline const char* BLUE          = "\033[1;34m";
    inline const char* YELLOW        = "\033[1;33m";
    inline const char* GRAY          = "\033[1;90m";
    inline const char* CYAN          = "\033[1;36m";
    inline const char* WHITE         = "\033[1;37m";

    // ── Console width helper ─────────────────────────────────────────────────
    inline int console_width()
    {
        CONSOLE_SCREEN_BUFFER_INFO csbi;
        if (GetConsoleScreenBufferInfo(GetStdHandle(STD_OUTPUT_HANDLE), &csbi))
            return csbi.srWindow.Right - csbi.srWindow.Left + 1;
        return 80; // safe fallback
    }

    // Visual width of the ASCII art banner (on-screen chars, NOT UTF-8 bytes).
    // Every other element uses this same value so they all share one center column.
    static constexpr int ART_VISUAL_WIDTH = 72;

    // Divider string (plain ASCII, safe to use text.size() for width)
    inline std::string divider() { return std::string(ART_VISUAL_WIDTH, '-'); }

    // ── Print a string centered in the console ───────────────────────────────
    // visual_len: the real on-screen character count.
    //   - Pass ART_VISUAL_WIDTH for anything that must align with the banner.
    //   - Leave -1 for plain ASCII strings (uses text.size()).
    inline void print_centered(const std::string& text,
                               const char* color      = nullptr,
                               int         visual_len = -1)
    {
        int w   = console_width();
        int len = (visual_len >= 0) ? visual_len : static_cast<int>(text.size());
        int pad = (w > len) ? (w - len) / 2 : 0;
        if (color) std::cout << color;
        std::cout << std::string(pad, ' ') << text;
        if (color) std::cout << RESET;
    }

    // ────────────────────────────────────────────────────────────────────────
    //  ANIMATION UTILITIES
    // ────────────────────────────────────────────────────────────────────────

    // Hide / show the blinking cursor during animations
    inline void cursor_visible(bool visible)
    {
        CONSOLE_CURSOR_INFO cci;
        GetConsoleCursorInfo(GetStdHandle(STD_OUTPUT_HANDLE), &cci);
        cci.bVisible = visible ? TRUE : FALSE;
        SetConsoleCursorInfo(GetStdHandle(STD_OUTPUT_HANDLE), &cci);
    }

    // Typewriter effect: prints `text` one character at a time
    // delay_ms is the pause between characters (default 18 ms)
    inline void type_print(const std::string& text,
                           const char* color    = nullptr,
                           int         delay_ms = 18)
    {
        if (color) std::cout << color;
        for (char c : text) {
            std::cout << c << std::flush;
            Sleep(delay_ms);
        }
        if (color) std::cout << RESET;
    }

    // Typewriter, centered — measures full string first, then types it in place
    inline void type_print_centered(const std::string& text,
                                    const char* color    = nullptr,
                                    int         delay_ms = 18,
                                    int         visual_len = -1)
    {
        int w   = console_width();
        int len = (visual_len >= 0) ? visual_len : static_cast<int>(text.size());
        int pad = (w > len) ? (w - len) / 2 : 0;
        std::cout << std::string(pad, ' ');
        type_print(text, color, delay_ms);
    }

    // Spinner: runs for `duration_ms` milliseconds with a label beside it.
    // Clears the line when done.
    inline void spinner(const std::string& label, int duration_ms = 800)
    {
        const char* frames[] = { "⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏" };
        const int   nframes  = 10;
        const int   frame_ms = 80;
        int         cycles   = duration_ms / frame_ms;

        cursor_visible(false);
        for (int i = 0; i < cycles; i++) {
            std::cout << "\r" << CLAUDE_ORANGE
                      << frames[i % nframes] << "  "
                      << GRAY << label
                      << RESET << std::flush;
            Sleep(frame_ms);
        }
        // Clear the spinner line
        std::cout << "\r" << std::string(label.size() + 6, ' ') << "\r" << std::flush;
        cursor_visible(true);
    }

    // Fade-in: prints each line with a brief delay between them,
    // giving a "drawing" feel to the ASCII banner
    inline void fade_print_lines(const std::vector<std::string>& lines,
                                 const char* color,
                                 int delay_ms  = 40,
                                 int visual_len = -1)
    {
        for (const auto& line : lines) {
            print_centered(line, color, visual_len);
            std::cout << "\n" << std::flush;
            Sleep(delay_ms);
        }
    }

    // ────────────────────────────────────────────────────────────────────────
    //  SPLASH SCREEN
    // ────────────────────────────────────────────────────────────────────────
    inline void display_splash()
    {
        cursor_visible(false);

        // ASCII art — each line is ART_VISUAL_WIDTH chars wide on screen.
        // Multi-byte UTF-8 box chars mean text.size() != visual width,
        // so we pass ART_VISUAL_WIDTH explicitly to every centering call.
        std::vector<std::string> art = {
            " \u2588\u2588\u2588\u2588\u2588\u2588\u2557  \u2588\u2588\u2588\u2588\u2588\u2588\u2557  \u2588\u2588\u2588\u2588\u2588\u2588\u2557     \u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2557\u2588\u2588\u2557  \u2588\u2588\u2557\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2557\u2588\u2588\u2557     \u2588\u2588\u2557     ",
            " \u2588\u2588\u2554\u2550\u2550\u2588\u2588\u2557\u2588\u2588\u2554\u2550\u2550\u2550\u2588\u2588\u2557\u2588\u2588\u2554\u2550\u2550\u2550\u2550\u255d     \u2588\u2588\u2554\u2550\u2550\u2550\u2550\u255d\u2588\u2588\u2551  \u2588\u2588\u2551\u2588\u2588\u2554\u2550\u2550\u2550\u2550\u255d\u2588\u2588\u2551     \u2588\u2588\u2551     ",
            " \u2588\u2588\u2588\u2588\u2588\u2588\u2554\u255d\u2588\u2588\u2551   \u2588\u2588\u2551\u2588\u2588\u2551  \u2588\u2588\u2588\u2557    \u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2557\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2551\u2588\u2588\u2588\u2588\u2588\u2557  \u2588\u2588\u2551     \u2588\u2588\u2551     ",
            " \u2588\u2588\u2554\u2550\u2550\u2588\u2588\u2557\u2588\u2588\u2551   \u2588\u2588\u2551\u2588\u2588\u2551   \u2588\u2588\u2551    \u255a\u2550\u2550\u2550\u2550\u2588\u2588\u2551\u2588\u2588\u2554\u2550\u2550\u2588\u2588\u2551\u2588\u2588\u2554\u2550\u2550\u255d  \u2588\u2588\u2551     \u2588\u2588\u2551     ",
            " \u2588\u2588\u2551  \u2588\u2588\u2551\u255a\u2588\u2588\u2588\u2588\u2588\u2588\u2554\u255d\u255a\u2588\u2588\u2588\u2588\u2588\u2588\u2554\u255d    \u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2551\u2588\u2588\u2551  \u2588\u2588\u2551\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2557\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2557\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2557",
            " \u255a\u2550\u255d  \u255a\u2550\u255d \u255a\u2550\u2550\u2550\u2550\u2550\u255d  \u255a\u2550\u2550\u2550\u2550\u2550\u255d     \u255a\u2550\u2550\u2550\u2550\u2550\u2550\u255d\u255a\u2550\u255d  \u255a\u2550\u255d\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u255d\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u255d\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u255d",
        };

        // Subtitle and divider are plain ASCII — measure normally
        const std::string subtitle  = "Native Windows Kernel Interface  |  Version 1.1";
        const std::string div_line  = std::string(ART_VISUAL_WIDTH, '-');
        const std::string ref_title = "QUICK REFERENCE";

        std::cout << "\n";
        fade_print_lines(art, CLAUDE_ORANGE, 45, ART_VISUAL_WIDTH);
        std::cout << "\n";

        // Subtitle and divider — centered to ART_VISUAL_WIDTH so they line up with the banner
        type_print_centered(subtitle, GRAY, 14, ART_VISUAL_WIDTH);
        std::cout << "\n";
        print_centered(div_line, GRAY); // div_line is plain ASCII, size() == visual width
        std::cout << "\n\n";

        spinner("Initialising RogShell...", 900);

        // "QUICK REFERENCE" title — centered to ART_VISUAL_WIDTH
        type_print_centered(ref_title, YELLOW, 20, ART_VISUAL_WIDTH);
        std::cout << "\n";
        print_centered(div_line, GRAY);
        std::cout << "\n";

        // Two-column command grid.
        // Total row width = 38 + 5 (separator) + 38 = 81 chars, which is close
        // to ART_VISUAL_WIDTH — we clamp to ART_VISUAL_WIDTH for the left pad.
        struct Row { const char* left; const char* right; };
        Row rows[] = {
            { "tasks   - running processes",  "sys     - RAM usage"           },
            { "sysfetch- full system info",   "whoami  - current user"        },
            { "time    - current date/time",  "cd      - change directory"    },
            { "list    - list directory",     "mkdir   - make directory"      },
            { "rm      - remove file",        "open    - read file"           },
            { "write   - write to file",      "echo    - echo text"           },
            { "calc    - arithmetic",         "dencalc - density calculator"  },
            { "base64  - encode / decode",    "help    - full command help"   },
        };

        // Each row is 38 + 5 + 38 = 81 chars wide; center it like the art
        const int ROW_WIDTH = 81;
        int w = console_width();
        int row_pad = (w > ROW_WIDTH) ? (w - ROW_WIDTH) / 2 : 0;

        for (auto& r : rows) {
            std::cout << std::string(row_pad, ' ')
                      << WHITE << std::left;
            // left column, fixed 38 chars
            char lbuf[40]; snprintf(lbuf, sizeof(lbuf), "%-38s", r.left);
            char rbuf[40]; snprintf(rbuf, sizeof(rbuf), "%-38s", r.right);
            std::cout << lbuf
                      << GRAY  << "  |  "
                      << WHITE << rbuf
                      << RESET << "\n" << std::flush;
            Sleep(35);
        }

        std::cout << "\n";
        print_centered(div_line, GRAY);
        std::cout << "\n\n" << std::flush;

        cursor_visible(true);
    }

    // ────────────────────────────────────────────────────────────────────────
    //  PROMPT
    // ────────────────────────────────────────────────────────────────────────
    inline void print_prompt()
    {
        char  user[UNLEN + 1];
        DWORD uLen = UNLEN + 1;
        char  cwd[MAX_PATH];

        if (!GetUserNameA(user, &uLen))
            strcpy(user, "User");
        GetCurrentDirectoryA(MAX_PATH, cwd);

        // Modern CLI two-line prompt
        std::cout << GREEN << user << RESET << " in " << BLUE << cwd << RESET;
        // \xBB is the "»" symbol in many encodings
        std::cout << "\n" << YELLOW << " \xBB " << RESET;
    }

} // End of namespace UI
