#include <windows.h>
#include <stdio.h>
#include "../include/core.h"

void execute_external_command(const char* command) {
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;

    // Initialize memory for the structures
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));

    // CreateProcessA arguments:
    // NULL: No specific module (use command line)
    // command: The string to execute
    // ... handles and flags ...
    if (CreateProcessA(NULL, (LPSTR)command, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi)) {
        
        // Wait for the launched app to finish before returning control to shell
        WaitForSingleObject(pi.hProcess, INFINITE);

        // Clean up handles to prevent memory leaks
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
    } else {
        printf("Native Error: Could not launch '%s'. Code: %lu\n", command, GetLastError());
    }
}