#!/usr/bin/env bash
set -euo pipefail
: "${MON_IP:?}" "${TRUSTED_IPS:?}"
iptables -N LAB_MONITORING 2>/dev/null || true
iptables -F LAB_MONITORING
iptables -A LAB_MONITORING -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
for src in $TRUSTED_IPS; do iptables -A LAB_MONITORING -s "$src/32" -j RETURN; done
iptables -A LAB_MONITORING -j DROP
for port in 18080 3100 3200 3000 4317 4318 4040; do
  iptables -C DOCKER-USER -p tcp -m conntrack --ctorigdst "$MON_IP" --ctorigdstport "$port" -j LAB_MONITORING 2>/dev/null || iptables -I DOCKER-USER 1 -p tcp -m conntrack --ctorigdst "$MON_IP" --ctorigdstport "$port" -j LAB_MONITORING
done

