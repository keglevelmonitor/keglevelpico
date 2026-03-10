## 💻 KegLevel Pico Project
 
The **KegLevel Pico** app monitors and tracks the level of beer in up to 5 kegs using a **Raspberry Pi Pico W** as the sensor hub. It communicates with the Pico W over your local network via Wi-Fi — no GPIO wiring to the host computer required.

Currently tested on Windows 10/11. Also runs on Raspberry Pi (Trixie/Bookworm) and macOS for testing.

Please **donate $$** if you use the app. 

![Support QR Code](src/assets/support.gif)

## 💻 Suite of Apps for the Home Brewer
**🔗 [KettleBrain Project](https://github.com/keglevelmonitor/kettlebrain)** An electric brewing kettle control system

**🔗 [FermVault Project](https://github.com/keglevelmonitor/fermvault)** A fermentation chamber control system

**🔗 [KegLevel Lite Project](https://github.com/keglevelmonitor/keglevel_lite)** A keg level monitoring system (GPIO version)

**🔗 [KegLevel Pico Project](https://github.com/keglevelmonitor/keglevelpico)** A keg level monitoring system (Pico W version)

**🔗 [BatchFlow Project](https://github.com/keglevelmonitor/batchflow)** A homebrew batch management system

**🔗 [TempMonitor Project](https://github.com/keglevelmonitor/tempmonitor)** A temperature monitoring and charting system


## To Install the App on Linux / Raspberry Pi

Open **Terminal** and run this command. Type carefully and use proper uppercase / lowercase because it matters:

```bash
bash <(curl -sL bit.ly/keglevel-pico)
```

That's it! You will now find the app in your application menu under **Other**. You can use the "Check for Updates" function inside the app to install future updates.

## To TEST the App in the Windows Environment

On a Windows 10+ computer, open **Command Prompt** and run this command. Type carefully and use proper uppercase / lowercase because it matters:

```bash
curl -sL bit.ly/keglevel-pico-win -o setup.bat && setup.bat
```

## To TEST the App on a Mac

On a macOS computer, open **Terminal** and run this command. Type carefully and use proper uppercase / lowercase because it matters:

```bash
bash <(curl -sL bit.ly/keglevel-pico-mac)
```

Note that you may need to install developer tools (free) to complete the installation. Just follow the prompts to install the developer tools, then run the bash command again to install the app on the Mac. (Beware it can take 5-10 minutes to install the developer tools on the Mac.)

You will find the app launcher in **Finder → Home → Applications → KegLevel Pico**.

## 🔗 Detailed installation instructions

👉 (placeholder for detailed installation instructions)

## ⚙️ Summary hardware requirements

Required
* Raspberry Pi Pico W (firmware in the KegLevelPicoOnly repo)
* Windows / Linux / macOS host computer running the KegLevel Pico app

## ⚙️ Hardware Requirements

For the complete list of required hardware, part numbers, and purchasing links, please see the detailed hardware list:

➡️ **[View Detailed Hardware List](src/assets/hardware.md)**

## To uninstall the App

To uninstall, run the same command as installation. When the menu appears, type **UNINSTALL**.

## ⚙️ For reference
Installed file structure:

```
~/keglevel_pico/
|-- utility files...
|-- src/
|   |-- application files...
|   |-- assets/
|       |-- supporting files...
|-- venv/
|   |-- python3 & dependencies
~/keglevel_pico-data/
|-- user data...
    
Required system-level dependencies are installed via sudo apt outside of venv.

```


