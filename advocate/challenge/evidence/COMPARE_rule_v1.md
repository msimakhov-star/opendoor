# rule_off vs rule_on

advocate.py identical across every call: **True** (sha256 `0bd552801e0557d4`)  
Same verdicts in the same order: **True**  
rule_off at 2026-09-19T13:37:18+00:00, rule_on at 2026-09-19T13:41:56+00:00

## note (5 vs 5 ok runs)

| metric | rule_off | rule_on | change |
| --- | --- | --- | --- |
| output_tokens | 146 [131 to 155] | 121 [112 to 167] | -25 (-17.1%) |
| input_tokens | 179 [169 to 182] | 345 [335 to 348] | +166 (+92.7%) |
| latency_s | 11.192 [9.513 to 17.209] | 11.16 [7.892 to 33.054] | -0.032 (-0.3%) |
| word_count | 126 [98 to 131] | 100 [93 to 108] | -26 (-20.6%) |
| sentence_count | 10 [9 to 11] | 11 [10 to 13] | +1 (+10.0%) |
| flesch_reading_ease | 59.4 [42.8 to 63.2] | 69.8 [60.4 to 74.9] | +10.4 (+17.5%) |
| flesch_kincaid_grade | 7.9 [7.6 to 10.1] | 6.2 [5 to 6.8] | -1.7 (-21.5%) |
| banned_total | 0 [0 to 0] | 0 [0 to 0] | +0 (n/a) |
| has_nhs_url | 0 of 5 | 3 of 5 | +3 (n/a) |
| contains_quote | 0 of 5 | 2 of 5 | +2 (n/a) |
| contains_nhs_quote | 0 of 5 | 0 of 5 | +0 (n/a) |
| placeholders | 3 [3 to 5] | 1 [0 to 2] | -2 (-66.7%) |

Most different note pair: run 3, Example Surgery C

| rule_off (trace `01a0b9e0ffa8faf21e852b369cec1eaa`) | rule_on (trace `01a0b9e5748c08a31fa5036d4d0ba818`) |
| --- | --- |
| Dear [Patient's Name],<br><br>We have noticed that the registration process for Example Surgery C, as described on their website, asks for photo ID and proof of address (such as a utility bill or bank statement), which is not strictly required according to the latest NHS guidance. The NHS recommends that you do not need to provide ID, proof of address, or proof of immigration status when registering with a GP surgery.<br><br>If you have any concerns or questions about this, please feel free to contact our practice directly. We are here to assist you and ensure that the registration process is as smooth and straightforward as possible.<br><br>Best regards,<br><br>[Your Name]  <br>[Your Position]  <br>Example Surgery C | Dear Patient,<br><br>When you hand in your registration form, please bring photo ID and a recent utility bill or bank statement if you have them, as this helps us to match your medical records.<br><br>However, the NHS guidance on nhs.uk states that you do not need ID, proof of address, or proof of immigration status to register.<br><br>We understand that the practice website page may be confusing. Please update the page to align with the NHS guidance.<br><br>Thank you for your understanding.<br><br>Sincerely,<br>[Your Name]<br>Example Surgery C<br><br>Please update the page. |

## letter (5 vs 5 ok runs)

| metric | rule_off | rule_on | change |
| --- | --- | --- | --- |
| output_tokens | 367 [310 to 387] | 156 [137 to 189] | -211 (-57.5%) |
| input_tokens | 180 [170 to 183] | 346 [336 to 349] | +166 (+92.2%) |
| latency_s | 30.565 [22.139 to 45.604] | 20.64 [11.883 to 23.876] | -9.925 (-32.5%) |
| word_count | 270 [257 to 301] | 133 [116 to 142] | -137 (-50.7%) |
| sentence_count | 19 [19 to 22] | 12 [10 to 12] | -7 (-36.8%) |
| flesch_reading_ease | 53.2 [47 to 61.4] | 65.8 [63.8 to 71] | +12.6 (+23.7%) |
| flesch_kincaid_grade | 8.9 [8.2 to 10.6] | 6.8 [6.6 to 7.2] | -2.1 (-23.6%) |
| banned_total | 0 [0 to 0] | 0 [0 to 0] | +0 (n/a) |
| has_nhs_url | 4 of 5 | 2 of 5 | -2 (-50.0%) |
| contains_quote | 5 of 5 | 5 of 5 | +0 (+0.0%) |
| contains_nhs_quote | 5 of 5 | 4 of 5 | -1 (-20.0%) |
| placeholders | 5 [5 to 5] | 1 [1 to 2] | -4 (-80.0%) |

Most different letter pair: run 4, Example Surgery D

| rule_off (trace `01a0b9e1bec9f1ca78236bc373878cad`) | rule_on (trace `01a0b9e678de5e2a70128988d125a42d`) |
| --- | --- |
| Subject: Website Compliance with NHS Guidance on Patient Registration<br><br>Dear [Practice Manager's Name],<br><br>I hope this letter finds you well. I recently conducted an automated review of the website for Example Surgery D, and I noticed a discrepancy between your practice’s website and the official NHS guidance regarding patient registration.<br><br>Specifically, the page "Registration" on your website states: "We ask all new patients to show one form of identification and one proof of address at reception so that we can check you live within our practice area." However, the NHS guidance clearly states: "You do not need ID, proof of address or proof of immigration status."<br><br>This discrepancy could potentially lead to confusion for patients and may not align with the official NHS standards. It is important that our practice adheres to the latest and most accurate guidance to ensure that all patients are treated fairly and with the appropriate level of care.<br><br>I recommend that you review the content on your website and update it to reflect the current NHS guidance. This will help ensure that all patients are aware of the requirements and are treated consistently and fairly.<br><br>If you have any questions or need further clarification, please do not hesitate to contact me.<br><br>Thank you for your attention to this matter.<br><br>Best regards,<br><br>[Your Name]  <br>[Your Position]  <br>[Your Contact Information]  <br>[Your Organization]  <br><br>---<br><br>[Optional: Include a link to the NHS guidance for easy reference.]<br><br>---<br><br>This letter should help bring the issue to the attention of the practice manager and prompt them to make the necessary changes to their website. | Dear Practice Manager,<br><br>We noticed that the registration page on your website asks new patients to show identification and proof of address at reception. However, the NHS guidance on their website states that you do not need ID, proof of address, or proof of immigration status to register.<br><br>The sentence from your website says, "We ask all new patients to show one form of identification and one proof of address at reception so that we can check you live within our practice area."<br><br>The NHS guidance page says, "You do not need ID, proof of address or proof of immigration status."<br><br>This seems to contradict the NHS guidance. Could you please update the page to reflect the correct information?<br><br>Thank you!<br><br>Best regards,<br>[Your Name]  <br>[Your Contact Information]  <br><br>Request: Please update the page to reflect the correct information from the NHS guidance. |
