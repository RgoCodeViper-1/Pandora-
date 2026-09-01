#pragma once
#include <vector>
#include <string>

class ShellParser {
public:
    // Core tokenizer / dispatcher
    static std::vector<std::string> tokenize(const std::string& input);
    static bool has_flag(const std::vector<std::string>& args, const std::string& flag);
    static void dispatch(const std::vector<std::string>& args);

    // ── Built-in commands (original RogShell) ──────────────────────────────
    // tasks, sys, cd, list  →  implemented inside dispatch()

    // ── Commands ported from Termi ─────────────────────────────────────────
    static void cmd_echo    (const std::vector<std::string>& args);
    static void cmd_calc    (const std::vector<std::string>& args);
    static void cmd_base64  (const std::vector<std::string>& args);
    static void cmd_whoami  (const std::vector<std::string>& args);
    static void cmd_ttime   (const std::vector<std::string>& args);
    static void cmd_mkdir   (const std::vector<std::string>& args);
    static void cmd_rm      (const std::vector<std::string>& args);
    static void cmd_openfile(const std::vector<std::string>& args);
    static void cmd_writefile(const std::vector<std::string>& args);
    static void cmd_dencalc (const std::vector<std::string>& args);
    static void cmd_sysfetch(const std::vector<std::string>& args);
};
