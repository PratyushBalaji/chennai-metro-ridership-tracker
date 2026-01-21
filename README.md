# Chennai Metro Ridership Tracker

Scripts to build and maintain a dataset of Chennai Metro's ridership through time. 
Inspired by a similar project for Bengaluru's Namma Metro : https://github.com/thecont1/namma-metro-ridership-tracker

Temporarily, I will be using an internal (but publicly callable) CMRL API for data collection. 

I analysed the network traffic on CMRL's dashboard (Available here : https://commuters-data.chennaimetrorail.org/passengerflow) to identify the relevant APIs am using it to fetch information about metro service. 

However, in the near future, I will replace this with a selenium (or alternative browser automation tool) based control flow that will scrape the same data from the website as if it were a real human tediously copying numbers from the dashboard, as the original namma metro ridership tracker does.

## Disclaimer

This project is intended **strictly** for **research, educational and informational purposes**. The data and API usage demonstrated here and in related repositories are meant solely to help understand how CMRL metro services function and retrieve data. Any use of this information in personal or public applications may place unintended load on CMRL servers and disrupt official services.

**I do not accept any responsibility or liability for consequences arising from the misuse of this data or API. Use at your own discretion and respect the intended limits of the service.**
