# General Information

Naming across datasets is not consistent as each API returns slightly different response. This document is for clarification as to the naming and structure or datasets.

## Dataset Conventions :
- Dates are always in YYYY-MM-DD format
- Times are always in 24h HH:MM format
- Station identifiers are always stored as their official 3-letter station code. (Ex : SCC for Chennai Central, SAL for Alandur)
- Metro Lines are always referred to by their official corridor number (01 = Blue line, 02 = Green line, etc)

## Payment Aggregates / Totals and Breakdown :

### A. Physical modes of payment : 
1. Card / Stored Value Card
2. Singara Chennai Card / NCMC
3. Trip / Tourist / Group Card (Potential duplicates, no distinction available, but also usually 0 rides so shouldn't discernably affect totals)
4. Tokens (Officially deprecated means of payment, but still has data)

Total physical payments = Sum of 1-4

### B. ONDC modes of payment :
1. Uber
2. Rapido
3. Redbus
4. Miles and Kilometers
5. Namma Yatri / JusPay

Total ONDC payments = Sum of 1-5

### C. QR modes of payment :
1. Paper
2. Whatsapp
3. Paytm
4. PhonePe
5. Cumta
6. Event
7. Promotional Ride
8. Mobile
9. SVP \[Digital Store Value Pass\]
10. ONDC (Total of section **B**)

Total QR payments = Sum of 1-11

### Total Ridership :
Total ridership is the sum of the Physical modes of payment and QR modes of payment.

For all intents and purposes, Paper QRs are not considered as a physical payment mode and are put into the category of QR modes of payment.
