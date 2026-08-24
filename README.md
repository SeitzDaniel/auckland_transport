# 🚍🚆⛴️ Auckland Transport custom Integration for Home Assistant

Custom component for Home Assistant which uses the [Auckland Transport API](https://dev-portal.at.govt.nz/) 
- Monitor real-time bus, train, and ferry departure times.
- Display the next trip as a sensor.
- Access detailed upcoming trips via attributes.

## Installation
### HACS (recommended)

1. [Install HACS](https://hacs.xyz/docs/use/download/download/), if you did not already
2. [![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=SeitzDaniel&repository=auckland_transport&category=integration)
3. Install the Auckland Transport integration
4. Restart Home Assistant

### Manually

If you prefer to instal manually, copy custom_components/auckland_transport to your installation's config/custom_components directory.

## Configuration

### 1. Sign up for API key
* Go [here](https://dev-portal.at.govt.nz/) to sign up for a free API key.

### 2. Setup your first stop
* Enter your API key and Submit.

<img width="572" height="310" alt="image" src="https://github.com/user-attachments/assets/4401f4bf-e59e-4264-a29b-e993d3c1d574" />


* You can can filter and search for your stop.

<img width="568" height="480" alt="image" src="https://github.com/user-attachments/assets/9c6d334a-3c96-45c8-b0ab-55a64261707c" />


## Device/Sensor/Attributes

#### Each stop is setup as a device.
* With multiple sensors and attributes

<img width="1325" height="618" alt="image" src="https://github.com/user-attachments/assets/c1361b8b-2783-4318-806f-c1625569ff87" />


## ⚙️ Additional Settings

Additional settings can be configured for each device.

<img width="598" height="679" alt="image" src="https://github.com/user-attachments/assets/67c9a75b-97ec-4e22-a6a0-66a56691fc9e" />



## Custom Card

To visualize your stops you can get the custom card from [here](https://github.com/SeitzDaniel/auckland-transport-card).

<img width="837" height="766" alt="image" src="https://github.com/user-attachments/assets/6f58db0f-fe1d-4754-bb9c-3ffec736eeb7" />



## License

MIT © [Daniel Seitz](https://github.com/SeitzDaniel)




