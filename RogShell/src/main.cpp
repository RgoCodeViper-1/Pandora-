#include <windows.h>
#include <iostream>
#include <string>
#include <vector>
#include "parser.hpp"
#include "ui.hpp"

int main(int argc, char* argv[])
{
    setvbuf(stdout, NULL, _IONBF, 0); // Send output immediately (important for subprocess mode)

    // 1. UTF-8 for both input and output
    SetConsoleOutputCP(CP_UTF8);
    SetConsoleCP(CP_UTF8);

    bool        silentMode       = false;
    std::string commandToExecute = "";

    // 2. Parse command-line arguments
    for (int i = 1; i < argc; i++) {
        std::string arg = argv[i];
        if (arg == "--silent") {
            silentMode = true;
        }
        else if (arg == "--run" && i + 1 < argc) {
            commandToExecute = argv[i + 1];
            i++;
        }
    }

    // 3. One-shot mode  (parent process passes --run "command")
    if (!commandToExecute.empty()) {
        auto args = ShellParser::tokenize(commandToExecute);
        ShellParser::dispatch(args);
        return 0;
    }

    // 4. Interactive mode
    if (!silentMode) {
        SetConsoleTitleA("RogShell v1.1");
        UI::display_splash();
    }

    std::string input;
    while (true) {
        UI::print_prompt();
        if (!std::getline(std::cin, input) || input == "exit") break;
        if (input.empty()) continue;

        auto args = ShellParser::tokenize(input);
        ShellParser::dispatch(args);
    }

    return 0;
}
