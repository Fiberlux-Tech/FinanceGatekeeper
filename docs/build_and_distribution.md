Finance Gatekeeper OS: Build & Distribution Guide

1. The Packaging Engine: PyInstaller vs. Nuitka

Most people start with PyInstaller, which bundles your code and the Python interpreter into one file. However, for a Financial OS, we should consider Nuitka.

PyInstaller: Great for "one-file" builds, but it is essentially a self-extracting zip. It can be slow to start (violating our < 3s startup target).

Nuitka: Actually translates your Python code into C++ and compiles it. It is much faster and harder to reverse-engineer, which is better for "Financial Integrity."

2. Handling the "CustomTkinter" Assets

Because CustomTkinter uses JSON files and images for its themes (the "Vibe"), we have to explicitly tell the compiler to include these assets. If we don't, the .exe will crash on startup because it can't find its "look and feel."

3. Hiding the "Black Box" (Console)

By default, running a Python script opens a black command prompt window. For a professional tool, we configure the build to be windowed. This ensures the user only sees the modern GUI, not the background terminal.

4. The Icon and Metadata

To make it look like a real OS component in your taskbar, we attach a custom .ico file and embed version metadata (e.g., Finance Gatekeeper OS v1.0.0). This satisfies the Professionalism pillar defined in CLAUDE.md.

5. Managing the .env and Credentials

This is the most important part: We never bundle the .env file inside the .exe.

The .exe should look for the .env file in the same folder where it is running, or we use a more secure method like Windows Credential Manager.

This allows you to update your Supabase keys without having to re-compile the entire program.

6. The "S-Tier" Distribution (The Installer)

Instead of just sending a raw .exe file (which Windows SmartScreen often blocks), the ultimate professional move is to use Inno Setup or NSIS to create a proper "Setup.exe" installer. This puts the app in Program Files, creates a Desktop shortcut, and adds an uninstaller in the Control Panel.

7. Current Recommendation

We should focus on finishing the code first, but keep the architecture "clean" (using absolute imports and external configs) so that when we run the compiler, it works perfectly on the first try.
