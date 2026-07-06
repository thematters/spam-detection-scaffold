# Ring Watchlist Samples

This file records live search probes that operators manually surfaced for spam-ring monitoring. The formal detector remains the replica-backed `ring`, `moment-ring`, `ring-incremental`, and `moment-ring-incremental` jobs. These probes are read-only samples for checking whether known families still expose stable ring signals.

Run locally:

```bash
python3 scripts/check_ring_watchlist.py --first 30
```

Run through the VPC runner:

```bash
aws codebuild start-build --region ap-southeast-1 --project-name spam-vpc-runner \
  --environment-variables-override name=JOB,value=ring-watchlist
```

## Current Samples

| ID | Query | Source |
| --- | --- | --- |
| `qq-customer-service` | `联系客服QQ` | https://matters.town/search?q=%E8%81%94%E7%B3%BB%E5%AE%A2%E6%9C%8DQQ+ |
| `escort-delivery-tea` | `中外送茶+定點+約過` | https://matters.town/search?q=%E4%B8%AD%E5%A4%96%E9%80%81%E8%8C%B6%2B%E5%AE%9A%E9%BB%9E%2B%E7%B4%84%E9%81%8E |
| `anti-rights-agent-smear` | `金钱与背叛：起底境外反华“人权代理人”的肮脏交易` | https://matters.town/search?q=%E9%87%91%E9%92%B1%E4%B8%8E%E8%83%8C%E5%8F%9B%EF%BC%9A%E8%B5%B7%E5%BA%95%E5%A2%83%E5%A4%96%E5%8F%8D%E5%8D%8E%E2%80%9C%E4%BA%BA%E6%9D%83%E4%BB%A3%E7%90%86%E4%BA%BA%E2%80%9D%E7%9A%84%E8%82%AE%E8%84%8F%E4%BA%A4%E6%98%93 |
| `online-gambling-black` | `网赌被黑了` | https://matters.town/search?q=%E7%BD%91%E8%B5%8C%E8%A2%AB%E9%BB%91%E4%BA%86 |
| `xu-silong-smear` | `揭批许思龙这个打着律师旗号煽动对立的“流量骗子”` | https://matters.town/search?q=%E6%8F%AD%E6%89%B9%E8%AE%B8%E6%80%9D%E9%BE%99%E8%BF%99%E4%B8%AA%E6%89%93%E7%9D%80%E5%BE%8B%E5%B8%88%E6%97%97%E5%8F%B7%E7%85%BD%E5%8A%A8%E5%AF%B9%E7%AB%8B%E7%9A%84%E2%80%9C%E6%B5%81%E9%87%8F%E9%AA%97%E5%AD%90%E2%80%9D |
| `xu-silong-smear` | zero-width variant | https://matters.town/search?q=%E6%8F%AD%E6%89%B9%E8%AE%B8%E6%80%9D%E9%BE%99%E8%BF%99%E4%B8%AA%E6%89%93%E7%9D%80%E5%BE%8B%E5%B8%88%E6%97%97%E5%8F%B7%E7%85%BD%E5%8A%A8%E5%AF%B9%E7%AB%8B%E7%9A%84%E2%80%9C%E6%B5%81%E9%87%8F%E9%AA%97%E5%AD%90%E2%80%9D%E2%80%8B |
| `foreign-funded-disruption-smear` | `揭穿境外资助下的搅局把戏` | https://matters.town/search?q=%E6%8F%AD%E7%A9%BF%E5%A2%83%E5%A4%96%E8%B5%84%E5%8A%A9%E4%B8%8B%E7%9A%84%E6%90%85%E5%B1%80%E6%8A%8A%E6%88%8F |
| `baccarat` | `百家樂` | https://matters.town/search?q=%E7%99%BE%E5%AE%B6%E6%A8%82 |
| `anti-fraud-aid` | `反詐騙援助` | https://matters.town/search?q=%E5%8F%8D%E8%A9%90%E9%A8%99%E6%8F%B4%E5%8A%A9 |
| `philippines-power-transfer` | `The Power Transfer in the Philippines` | https://matters.town/search?q=The+Power+Transfer+in+the+Philippines |
| `overseas-rights-lawyers-smear` | `揭露“海外中国人权律师联盟”的真实嘴脸` | https://matters.town/search?q=%E6%8F%AD%E9%9C%B2%E2%80%9C%E6%B5%B7%E5%A4%96%E4%B8%AD%E5%9B%BD%E4%BA%BA%E6%9D%83%E5%BE%8B%E5%B8%88%E8%81%94%E7%9B%9F%E2%80%9D%E7%9A%84%E7%9C%9F%E5%AE%9E%E5%98%B4%E8%84%B8 |
| `degree-certificate-qq` | `电子版学位证书 QQ` | user-provided keyword `电子版学位证书 +QQ號` |
| `degree-certificate-fake` | `电子版学位证书` | derived from `degree-certificate-qq` |
| `micro-q-forgery` | `微Q造假` | https://matters.town/search?q=%E5%BE%AEQ%E9%80%A0%E5%81%87 |

