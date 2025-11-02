# 🚍🚆⛴️ Auckland Transport custom Integration for Home Assistant

Custom component for Home Assistant which uses the [Auckland Transport API](https://dev-portal.at.govt.nz/) 
- Monitor real-time bus, train, and ferry departure times.
- Display the next trip as a sensor.
- Access detailed upcoming trips via attributes.
- Customize update intervals and quiet hours.

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

<img width="481" height="260" alt="image" src="https://github.com/user-attachments/assets/032b450e-66f4-4ce6-807a-ced9348553a7" />

* You can filter by Stop Type.

<img width="320" height="349" alt="image" src="https://github.com/user-attachments/assets/2499c7de-6b74-4df8-aad1-6bcb9e935520" />

* Select your stop from the list and submit.

<img width="316" height="226" alt="image" src="https://github.com/user-attachments/assets/6676fa2c-c96b-436c-adf3-3d4f923fec2b" />

## Sensor/Attributes

#### The main sensor is always the next trip.
* Within attributes it will list upcoming trips.

<img width="438" height="812" alt="image" src="https://github.com/user-attachments/assets/f83568be-ba5c-47cc-aeb0-9aadcd88d90b" />

## ⚙️ Additional Settings

<img width="624" height="261" alt="image" src="https://github.com/user-attachments/assets/f12aa157-e7ea-437f-95ef-312f8c99662b" />

You can change update interval, disable API calls during set times and adjust how many upcoming departures are included within the attributes.

<img width="421" height="456" alt="image" src="https://github.com/user-attachments/assets/ec5d5efe-d511-4dee-a314-3e163e0326b1" />






