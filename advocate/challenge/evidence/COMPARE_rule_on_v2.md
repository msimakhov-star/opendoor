# rule_off vs rule_on_v2

advocate.py identical across every call: **True** (sha256 `0bd552801e0557d4`)  
Same verdicts in the same order: **True**  
rule_off at 2026-09-19T13:37:18+00:00, rule_on_v2 at 2026-09-19T13:45:56+00:00

## note (5 vs 5 ok runs)

| metric | rule_off | rule_on_v2 | change |
| --- | --- | --- | --- |
| output_tokens | 146 [131 to 155] | 126 [77 to 161] | -20 (-13.7%) |
| input_tokens | 179 [169 to 182] | 432 [422 to 435] | +253 (+141.3%) |
| latency_s | 11.192 [9.513 to 17.209] | 12.357 [11.505 to 18.307] | +1.165 (+10.4%) |
| word_count | 126 [98 to 131] | 77 [67 to 96] | -49 (-38.9%) |
| sentence_count | 10 [9 to 11] | 6 [5 to 6] | -4 (-40.0%) |
| flesch_reading_ease | 59.4 [42.8 to 63.2] | 66.4 [54.9 to 71.8] | +7 (+11.8%) |
| flesch_kincaid_grade | 7.9 [7.6 to 10.1] | 7.9 [6 to 9.6] | +0 (+0.0%) |
| banned_total | 0 [0 to 0] | 0 [0 to 0] | +0 (n/a) |
| has_nhs_url | 0 of 5 | 4 of 5 | +4 (n/a) |
| contains_quote | 0 of 5 | 2 of 5 | +2 (n/a) |
| contains_nhs_quote | 0 of 5 | 0 of 5 | +0 (n/a) |
| placeholders | 3 [3 to 5] | 0 [0 to 0] | -3 (-100.0%) |

Most different note pair: run 3, Example Surgery C

| rule_off (trace `01a0b9e0ffa8faf21e852b369cec1eaa`) | rule_on_v2 (trace `01a0b9e919a219c63347006b27deb722`) |
| --- | --- |
| Dear [Patient's Name],<br><br>We have noticed that the registration process for Example Surgery C, as described on their website, asks for photo ID and proof of address (such as a utility bill or bank statement), which is not strictly required according to the latest NHS guidance. The NHS recommends that you do not need to provide ID, proof of address, or proof of immigration status when registering with a GP surgery.<br><br>If you have any concerns or questions about this, please feel free to contact our practice directly. We are here to assist you and ensure that the registration process is as smooth and straightforward as possible.<br><br>Best regards,<br><br>[Your Name]  <br>[Your Position]  <br>Example Surgery C | Note for a Patient:<br><br>The NHS guidance on their website (https://www.nhs.uk/nhs-services/gps/how-to-register-with-a-gp-surgery/) says you do not need to bring ID, proof of address, or proof of immigration status to register with a GP surgery.<br><br>However, Example Surgery C's website (https://example-surgery-c.example.org/join-the-practice/) suggests bringing photo ID and a recent utility bill or bank statement to help match your medical records.<br><br>You can show the NHS guidance link at reception if you have any questions.<br><br>Open Door |

## letter (5 vs 5 ok runs)

| metric | rule_off | rule_on_v2 | change |
| --- | --- | --- | --- |
| output_tokens | 367 [310 to 387] | 184 [140 to 270] | -183 (-49.9%) |
| input_tokens | 180 [170 to 183] | 433 [423 to 436] | +253 (+140.6%) |
| latency_s | 30.565 [22.139 to 45.604] | 15.7 [14.948 to 26.687] | -14.865 (-48.6%) |
| word_count | 270 [257 to 301] | 135 [125 to 188] | -135 (-50.0%) |
| sentence_count | 19 [19 to 22] | 10 [9 to 16] | -9 (-47.4%) |
| flesch_reading_ease | 53.2 [47 to 61.4] | 65.3 [58.6 to 69.6] | +12.1 (+22.7%) |
| flesch_kincaid_grade | 8.9 [8.2 to 10.6] | 7.4 [6.6 to 8.6] | -1.5 (-16.9%) |
| banned_total | 0 [0 to 0] | 0 [0 to 0] | +0 (n/a) |
| has_nhs_url | 4 of 5 | 4 of 5 | +0 (+0.0%) |
| contains_quote | 5 of 5 | 5 of 5 | +0 (+0.0%) |
| contains_nhs_quote | 5 of 5 | 5 of 5 | +0 (+0.0%) |
| placeholders | 5 [5 to 5] | 0 [0 to 0] | -5 (-100.0%) |

Most different letter pair: run 4, Example Surgery D

| rule_off (trace `01a0b9e1bec9f1ca78236bc373878cad`) | rule_on_v2 (trace `01a0b9e9cbd37a0b2868679be5fa546e`) |
| --- | --- |
| Subject: Website Compliance with NHS Guidance on Patient Registration<br><br>Dear [Practice Manager's Name],<br><br>I hope this letter finds you well. I recently conducted an automated review of the website for Example Surgery D, and I noticed a discrepancy between your practice’s website and the official NHS guidance regarding patient registration.<br><br>Specifically, the page "Registration" on your website states: "We ask all new patients to show one form of identification and one proof of address at reception so that we can check you live within our practice area." However, the NHS guidance clearly states: "You do not need ID, proof of address or proof of immigration status."<br><br>This discrepancy could potentially lead to confusion for patients and may not align with the official NHS standards. It is important that our practice adheres to the latest and most accurate guidance to ensure that all patients are treated fairly and with the appropriate level of care.<br><br>I recommend that you review the content on your website and update it to reflect the current NHS guidance. This will help ensure that all patients are aware of the requirements and are treated consistently and fairly.<br><br>If you have any questions or need further clarification, please do not hesitate to contact me.<br><br>Thank you for your attention to this matter.<br><br>Best regards,<br><br>[Your Name]  <br>[Your Position]  <br>[Your Contact Information]  <br>[Your Organization]  <br><br>---<br><br>[Optional: Include a link to the NHS guidance for easy reference.]<br><br>---<br><br>This letter should help bring the issue to the attention of the practice manager and prompt them to make the necessary changes to their website. | Dear Example Surgery D,<br><br>We noticed that your website states, "We ask all new patients to show one form of identification and one proof of address at reception so that we can check you live within our practice area." However, the NHS guidance on their website says, "You do not need ID, proof of address or proof of immigration status."<br><br>This seems to contradict the NHS guidance. The NHS page clearly states that you do not need to provide any of these documents to register with a GP surgery.<br><br>Could you please update your website to reflect the NHS guidance? This will help ensure that all patients are aware of the correct requirements for registration.<br><br>Thank you for your attention to this matter.<br><br>Open Door |
