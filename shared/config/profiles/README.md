## The later directory structure for the profiles implementation for Pandora

PANDORA/
└── shared/
    └── config/
        ├── metadata.json        #The info about Pandora
        ├── configuration.json   #The configuration setting
        ├── active-profile.json  #The profile loader
        │
        └── profiles/
            ├── dev.json 
            ├── prod.json
            ├── user_apela.json
            ├── guest.json
            ├── safe_mode.json
            └── lowpower.json


### More info about this 
_active-profile.json_

Tells startup which profile to load.

Example:

{
  "active": "user_apela"
}
🚀 **Real Use Cases**

1. *Developer Profile*
profiles/dev.json
{
  "runtime": {
    "debug": true,
    "verboseLogs": true
  },
  "security": {
    "allowShell": true
  },
  "ai": {
    "provider": "mock"
  }
}

👉 Fast testing mode.

2. *Production User Profile*
profiles/user_apela.json
{
  "runtime": {
    "debug": false
  },
  "audio": {
    "inputDevice": "Microphone Array"
  },
  "memory": {
    "userName": "Apela"
  }
}

👉 Personalized assistant.

3. *Guest Mode*
{
  "memory": {
    "temporary": true,
    "saveHistory": false
  }
}
4. *Low Power Laptop*
{
  "hardware": {
    "gpuEnabled": false
  },
  "ai": {
    "localModel": "tiny"
  }
}