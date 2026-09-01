/**
 * RogShell – commands.cpp
 *
 * Original RogShell commands by Bhaswar.
 * Extended with commands ported from Termi v3.3.0 (MIT License)
 * by ringwormGO-organization — https://github.com/ringwormGO-organization/Termi
 *
 * Termi commands adapted for RogShell:
 *   • AddLog() / Status() calls replaced with direct std::cout output
 *   • Windows-only path kept throughout (matching RogShell's target platform)
 *   • dirent.h included for POSIX-style directory traversal on Windows
 */

#include "parser.hpp"
#include "ui.hpp"
#include "../include/core.h" //#include "core.h"
#include "../include/base64.hpp" 
#include "../include/dirent.h"  // Termi's Windows-compatible dirent

#include <iostream>
#include <sstream>
#include <fstream>
#include <filesystem>
#include <algorithm>
#include <chrono>
#include <string>
#include <vector>

#include <windows.h>
#include <tlhelp32.h>
#include <lmcons.h>
#include <direct.h>
#include <sys/stat.h>
#include <intrin.h>          // __cpuid  (for sysfetch CPU details)

// ─────────────────────────────────────────────────────────────────────────────
//  Helpers shared by Termi-ported commands
// ─────────────────────────────────────────────────────────────────────────────

// Windows uptime helpers (used by sysfetch)
static uint64_t UptimeS() { return GetTickCount64() / 1000ULL; }
static uint64_t UptimeM() { return UptimeS() / 60ULL; }
static uint64_t UptimeH() { return UptimeM() / 60ULL; }

// Forward declaration
static void run_list(const std::vector<std::string>& args);
// Windows version string (used by sysfetch)
static const char* OperatingSystem()
{
    OSVERSIONINFOEX osvi{};
    osvi.dwOSVersionInfoSize = sizeof(osvi);
#pragma warning(suppress: 4996)
    GetVersionEx(reinterpret_cast<OSVERSIONINFO*>(&osvi));

    if (osvi.dwMajorVersion == 10 && osvi.dwBuildNumber >= 22000) return "Windows 11";
    if (osvi.dwMajorVersion == 10)                                 return "Windows 10";
    if (osvi.dwMajorVersion == 6 && osvi.dwMinorVersion == 3)      return "Windows 8.1";
    if (osvi.dwMajorVersion == 6 && osvi.dwMinorVersion == 2)      return "Windows 8";
    if (osvi.dwMajorVersion == 6 && osvi.dwMinorVersion == 1)      return "Windows 7";
    return "Windows (unknown version)";
}

// ─────────────────────────────────────────────────────────────────────────────
//  ShellParser core
// ─────────────────────────────────────────────────────────────────────────────

std::vector<std::string> ShellParser::tokenize(const std::string& input)
{
    std::vector<std::string> tokens;
    std::stringstream ss(input);
    std::string temp;
    while (ss >> temp) tokens.push_back(temp);
    return tokens;
}

bool ShellParser::has_flag(const std::vector<std::string>& args, const std::string& flag)
{
    return std::find(args.begin(), args.end(), flag) != args.end();
}

// ─────────────────────────────────────────────────────────────────────────────
//  dispatch() – command router
// ─────────────────────────────────────────────────────────────────────────────

void ShellParser::dispatch(const std::vector<std::string>& args)
{
    if (args.empty()) return;

    const std::string& cmd  = args[0];
    bool jsonMode            = has_flag(args, "--json");

    // ── 1. HELP ──────────────────────────────────────────────────────────────
    if (cmd == "help")
    {
        if (jsonMode) {
            std::cout << "{\"commands\":[\"cd\",\"list\",\"sys\",\"tasks\","
                         "\"echo\",\"calc\",\"base64\",\"whoami\",\"time\","
                         "\"mkdir\",\"rm\",\"open\",\"write\",\"dencalc\","
                         "\"sysfetch\",\"help\",\"exit\"]}" << std::endl;
        } else {
            std::cout << UI::YELLOW << "\n--- ROGSHELL COMMAND ROSTER ---" << UI::RESET << "\n"
                      << " ── System ──────────────────────────────────────────\n"
                      << " tasks              : Show running processes\n"
                      << " sys                : RAM usage summary\n"
                      << " sysfetch           : Full system info (CPU, RAM, OS)\n"
                      << " whoami             : Print current username\n"
                      << " time               : Print current date/time\n"
                      << " ── File system ─────────────────────────────────────\n"
                      << " cd <path>          : Change directory\n"
                      << " list [path]        : List directory contents\n"
                      << " mkdir <name>       : Create directory\n"
                      << " rm <file>          : Remove a file\n"
                      << " open <file>        : Print file contents\n"
                      << " write <file> <txt> : Write text to file\n"
                      << " ── Utilities ───────────────────────────────────────\n"
                      << " echo <text>        : Echo text back\n"
                      << " calc <op> <a> <b>  : Calculator  (op: + - * /)\n"
                      << " dencalc <m> <v> <d>: Density calc (use x for unknown)\n"
                      << " base64 -e|-d <str> : Encode / decode Base64\n"
                      << " ── Shell ───────────────────────────────────────────\n"
                      << " help               : This menu\n"
                      << " exit               : Quit RogShell\n" << std::endl;
        }
        return;
    }

    // ── 2. TASK MANAGER ──────────────────────────────────────────────────────
    if (cmd == "tasks")
    {
        HANDLE hSnap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        PROCESSENTRY32 pe{ sizeof(PROCESSENTRY32) };
        if (jsonMode) std::cout << "{\"tasks\":[";
        bool first = true;
        if (Process32First(hSnap, &pe)) {
            do {
                if (jsonMode) {
                    if (!first) std::cout << ",";
                    std::cout << "{\"name\":\"" << pe.szExeFile
                              << "\",\"id\":" << pe.th32ProcessID << "}";
                    first = false;
                } else {
                    std::cout << " [" << pe.th32ProcessID << "] " << pe.szExeFile << "\n";
                }
            } while (Process32Next(hSnap, &pe));
        }
        if (jsonMode) std::cout << "]}" << std::endl;
        CloseHandle(hSnap);
        return;
    }

    // ── 3. SYSTEM RAM SUMMARY ────────────────────────────────────────────────
    if (cmd == "sys")
    {
        MEMORYSTATUSEX ms{ sizeof(ms) };
        GlobalMemoryStatusEx(&ms);
        if (jsonMode) {
            std::cout << "{\"ram_usage\":" << ms.dwMemoryLoad << "}" << std::endl;
        } else {
            std::cout << UI::CLAUDE_ORANGE << "RAM Load: " << ms.dwMemoryLoad
                      << "%" << UI::RESET << std::endl;
        }
        return;
    }

    // ── 4. DIRECTORY NAVIGATION ──────────────────────────────────────────────
    if (cmd == "cd")
    {
        if (args.size() > 1)
            SetCurrentDirectoryA(args[1].c_str());
        else
            std::cout << "Usage: cd <path>\n";
        return;
    }

    // ── 5. LIST DIRECTORY (Termi-style dirent, Windows) ──────────────────────
    if (cmd == "list")
    {
        run_list(args);
        return;
    }

    // ── 6. ECHO ──────────────────────────────────────────────────────────────
    if (cmd == "echo")  { cmd_echo(args);      return; }

    // ── 7. CALCULATOR ────────────────────────────────────────────────────────
    if (cmd == "calc")  { cmd_calc(args);      return; }

    // ── 8. BASE64 ────────────────────────────────────────────────────────────
    if (cmd == "base64"){ cmd_base64(args);    return; }

    // ── 9. WHOAMI ────────────────────────────────────────────────────────────
    if (cmd == "whoami"){ cmd_whoami(args);    return; }

    // ── 10. TIME ─────────────────────────────────────────────────────────────
    if (cmd == "time")  { cmd_ttime(args);     return; }

    // ── 11. MKDIR ────────────────────────────────────────────────────────────
    if (cmd == "mkdir") { cmd_mkdir(args);     return; }

    // ── 12. RM ───────────────────────────────────────────────────────────────
    if (cmd == "rm")    { cmd_rm(args);        return; }

    // ── 13. OPEN (read file) ─────────────────────────────────────────────────
    if (cmd == "open")  { cmd_openfile(args);  return; }

    // ── 14. WRITE FILE ───────────────────────────────────────────────────────
    if (cmd == "write") { cmd_writefile(args); return; }

    // ── 15. DENSITY CALCULATOR ───────────────────────────────────────────────
    if (cmd == "dencalc"){ cmd_dencalc(args);  return; }

    // ── 16. SYSFETCH ─────────────────────────────────────────────────────────
    if (cmd == "sysfetch"){ cmd_sysfetch(args); return; }

    // ── 17. EXTERNAL FALLBACK ────────────────────────────────────────────────
    {
        std::string full;
        for (const auto& s : args)
            if (s.find("--") == std::string::npos) full += s + " ";
        execute_external_command(full.c_str());
    }
}

// ─────────────────────────────────────────────────────────────────────────────
//  LIST – directory listing (replaces the old stub, Termi-style with dirent)
// ─────────────────────────────────────────────────────────────────────────────

// Re-declare dispatch case 5 properly:
// (The list command accidentally forwarded above – patch that by overriding here)
// We'll hook in via the global init trick; instead just make ShellParser::dispatch
// call a free function.  The clean way: patch dispatch's list arm to call this.
// Because the file is compiled as one TU, we define it here and the dispatch
// block for "list" above is replaced with a direct call (fixed in final version).

static void run_list(const std::vector<std::string>& args)
{
    struct dirent* d;
    struct stat    dst;
    DIR*           dr;
    std::string    path = (args.size() > 1) ? args[1] : ".";

    _chdir(path.c_str());

    dr = opendir(".\\");
    if (!dr) {
        std::cout << UI::CLAUDE_ORANGE << "Error: cannot open directory '" << path << "'\n" << UI::RESET;
        return;
    }

    bool jsonMode = ShellParser::has_flag(args, "--json");
    if (jsonMode) std::cout << "{\"entries\":[";
    bool first = true;

    for (d = readdir(dr); d != nullptr; d = readdir(dr))
    {
        std::string name = d->d_name;
        std::string full = std::string(".\\") + name;
        std::string type = "UNKNOWN";

        if (stat(full.c_str(), &dst) == 0) {
            if (dst.st_mode & S_IFDIR) type = "DIR";
            else if (dst.st_mode & S_IFREG) type = "FILE";
        }

        if (jsonMode) {
            if (!first) std::cout << ",";
            std::cout << "{\"name\":\"" << name << "\",\"type\":\"" << type << "\"}";
            first = false;
        } else {
            std::string color = (type == "DIR") ? UI::BLUE : UI::RESET;
            std::cout << color << " " << std::left;
            std::cout.width(6); std::cout << type;
            std::cout << UI::RESET << "  " << name << "\n";
        }
    }

    if (jsonMode) std::cout << "]}" << std::endl;
    closedir(dr);
}

// ─────────────────────────────────────────────────────────────────────────────
//  Termi-ported command implementations
//  (AddLog → std::cout,  Status(n) → early return / print status)
// ─────────────────────────────────────────────────────────────────────────────

void ShellParser::cmd_echo(const std::vector<std::string>& args)
{
    if (args.size() < 2) { std::cout << "Usage: echo <text...>\n"; return; }
    for (size_t i = 1; i < args.size(); i++)
        std::cout << args[i] << " ";
    std::cout << "\n";
}

void ShellParser::cmd_calc(const std::vector<std::string>& args)
{
    if (args.size() < 4) {
        std::cout << "Usage: calc <+|-|*|/> <num1> <num2>\n";
        return;
    }
    try {
        const std::string& op = args[1];
        float a = std::stof(args[2]);
        float b = std::stof(args[3]);
        float r = 0.f;

        if      (op == "+") r = a + b;
        else if (op == "-") r = a - b;
        else if (op == "*") r = a * b;
        else if (op == "/") {
            if (b == 0) { std::cout << "Error: division by zero\n"; return; }
            r = a / b;
        } else {
            std::cout << "Error: unknown operator '" << op << "'\n";
            return;
        }
        std::cout << UI::CLAUDE_ORANGE << "Result: " << r << UI::RESET << "\n";
    }
    catch (const std::exception& e) {
        std::cout << "Exception: " << e.what() << "\n";
    }
}

void ShellParser::cmd_base64(const std::vector<std::string>& args)
{
    // Usage:  base64 -e <string>   OR   base64 -d <encoded>
    if (args.size() < 3) {
        std::cout << "Usage: base64 -e|-d <string>\n";
        return;
    }
    const std::string& flag = args[1];
    std::string result;

    if (flag == "-e") {
        for (size_t i = 2; i < args.size(); i++)
            result += base64_encode(args[i]) + " ";
        std::cout << "Encoded: " << result << "\n";
    }
    else if (flag == "-d") {
        for (size_t i = 2; i < args.size(); i++)
            result += base64_decode(args[i]) + " ";
        std::cout << "Decoded: " << result << "\n";
    }
    else {
        std::cout << "Error: unknown flag '" << flag << "' (use -e or -d)\n";
    }
}

void ShellParser::cmd_whoami(const std::vector<std::string>& args)
{
    char user[UNLEN + 1];
    DWORD len = UNLEN + 1;
    if (GetUserNameA(user, &len))
        std::cout << UI::GREEN << user << UI::RESET << "\n";
    else
        std::cout << "Error: could not retrieve username\n";
}

void ShellParser::cmd_ttime(const std::vector<std::string>& args)
{
    auto now = std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
    std::cout << UI::CLAUDE_ORANGE << ctime(&now) << UI::RESET;
}

void ShellParser::cmd_mkdir(const std::vector<std::string>& args)
{
    if (args.size() < 2) { std::cout << "Usage: mkdir <dirname>\n"; return; }
    if (_mkdir(args[1].c_str()) == -1)
        std::cout << "Error: could not create directory '" << args[1] << "'\n";
    else
        std::cout << "Directory '" << args[1] << "' created.\n";
}

void ShellParser::cmd_rm(const std::vector<std::string>& args)
{
    if (args.size() < 2) { std::cout << "Usage: rm <file>\n"; return; }
    if (std::remove(args[1].c_str()) != 0)
        std::cout << "Error: could not remove '" << args[1] << "'\n";
    else
        std::cout << "Removed '" << args[1] << "'\n";
}

void ShellParser::cmd_openfile(const std::vector<std::string>& args)
{
    // Dual use:
    //   dispatch("list", ...) → directory listing
    //   dispatch("open", ...) → file read
    if (args[0] == "list") { run_list(args); return; }

    if (args.size() < 2) { std::cout << "Usage: open <file>\n"; return; }
    std::ifstream f(args[1]);
    if (!f) { std::cout << "Error: no such file '" << args[1] << "'\n"; return; }
    std::string line;
    while (std::getline(f, line))
        std::cout << line << "\n";
    std::cout << "\n";
}

void ShellParser::cmd_writefile(const std::vector<std::string>& args)
{
    if (args.size() < 3) { std::cout << "Usage: write <file> <content>\n"; return; }

    const std::string& file    = args[1];
    std::string        content = args[2];
    // Concatenate remaining tokens (allows multi-word content without quotes)
    for (size_t i = 3; i < args.size(); i++) content += " " + args[i];

    std::ofstream f(file, std::ios::app);
    if (!f) { std::cout << "Error: cannot open '" << file << "' for writing\n"; return; }
    f << content << "\n";
    std::cout << "Written to '" << file << "'\n";
}

void ShellParser::cmd_dencalc(const std::vector<std::string>& args)
{
    // Usage:  dencalc <mass> <volume> <density>
    // Use 'x' for the unknown value.
    // density = mass / volume
    if (args.size() < 4) {
        std::cout << "Usage: dencalc <mass|x> <volume|x> <density|x>\n"
                     "       Use 'x' for the value you want to calculate.\n";
        return;
    }
    try {
        bool m_unk = (args[1] == "x");
        bool v_unk = (args[2] == "x");
        bool d_unk = (args[3] == "x");

        if (!m_unk && !v_unk && d_unk) {
            double r = std::stod(args[1]) / std::stod(args[2]);
            std::cout << "Density: " << r << " g/cm³\n";
        } else if (m_unk && !v_unk && !d_unk) {
            double r = std::stod(args[2]) * std::stod(args[3]);
            std::cout << "Mass: " << r << " g\n";
        } else if (!m_unk && v_unk && !d_unk) {
            double r = std::stod(args[1]) / std::stod(args[3]);
            std::cout << "Volume: " << r << " cm³\n";
        } else {
            std::cout << "Error: exactly one argument must be 'x'\n";
        }
    }
    catch (const std::exception& e) {
        std::cout << "Exception: " << e.what() << "\n";
    }
}

void ShellParser::cmd_sysfetch(const std::vector<std::string>& args)
{
    // ── User / Computer ───────────────────────────────────────────────────────
    char username[UNLEN + 1], computer[MAX_COMPUTERNAME_LENGTH + 1];
    DWORD uLen = UNLEN + 1, cLen = MAX_COMPUTERNAME_LENGTH + 1;
    GetUserNameA(username, &uLen);
    GetComputerNameA(computer, &cLen);

    // ── Memory ────────────────────────────────────────────────────────────────
    MEMORYSTATUSEX ms{ sizeof(ms) };
    GlobalMemoryStatusEx(&ms);
    float totalGB = static_cast<float>(ms.ullTotalPhys) / (1024.f * 1024.f * 1024.f);

    // ── CPU brand string via __cpuid ──────────────────────────────────────────
    char brand[64] = "Unknown";
    int  info[4]   = { -1 };
    __cpuid(info, 0x80000000);
    unsigned nExIds = static_cast<unsigned>(info[0]);
    if (nExIds >= 0x80000004) {
        char brandStr[0x40]{};
        __cpuid(reinterpret_cast<int*>(brandStr + 0),  0x80000002);
        __cpuid(reinterpret_cast<int*>(brandStr + 16), 0x80000003);
        __cpuid(reinterpret_cast<int*>(brandStr + 32), 0x80000004);
        // __cpuid writes 4 ints per call – use cpuidex pattern:
        int r[4];
        __cpuid(r, 0x80000002); memcpy(brandStr,      r, 16);
        __cpuid(r, 0x80000003); memcpy(brandStr + 16, r, 16);
        __cpuid(r, 0x80000004); memcpy(brandStr + 32, r, 16);
        // trim leading spaces
        const char* p = brandStr;
        while (*p == ' ') p++;
        strncpy_s(brand, sizeof(brand), p, _TRUNCATE);
    }

    // ── Print ──────────────────────────────────────────────────────────────────
    std::cout << "\n"
              << UI::CLAUDE_ORANGE << "  " << username << " @ " << computer << "\n"
              << "  ----------------------------------------\n" << UI::RESET
              << UI::GREEN  << "  OS      " << UI::RESET << OperatingSystem()  << "\n"
              << UI::GREEN  << "  Uptime  " << UI::RESET;
    if (UptimeH() < 1)
        std::cout << UptimeM() << " minutes\n";
    else
        std::cout << UptimeH() << " hours\n";

    std::cout << UI::GREEN  << "  CPU     " << UI::RESET << brand << "\n"
              << UI::GREEN  << "  RAM     " << UI::RESET << totalGB << " GB total  ("
              << ms.dwMemoryLoad << "% in use)\n\n";
}
