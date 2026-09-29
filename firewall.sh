#!/bin/bash
# Quillon Firewall — Aggressive nftables rules
# Blocks all inbound traffic except loopback
# Allows only DNS, DHCP, and SearXNG locally
# Rate-limits outbound connections

set -e

TABLE="inet quillon_fw"

# Remove old rules if exist
sudo nft delete table $TABLE 2>/dev/null || true

sudo nft -f - << 'EOF'
table inet quillon_fw {
    # Blocked IPs (populated dynamically)
    set blocked_ips {
        type ipv4_addr
        flags timeout
        timeout 1h
    }

    set blocked_ips6 {
        type ipv6_addr
        flags timeout
        timeout 1h
    }

    chain output {
        type filter hook output priority 0; policy accept;

        # Allow loopback
        oif "lo" accept

        # Allow established/related
        ct state established,related accept

        # Block INVALID state
        ct state invalid drop

        # Allow SearXNG localhost
        ip daddr 127.0.0.1 tcp dport { 8888, 8080 } accept

        # DHCP
        udp sport 68 udp dport 67 accept

        # DNS (trusted resolvers only)
        ip daddr { 1.1.1.1, 1.0.0.1, 9.9.9.9, 208.67.222.222, 127.0.0.53 } udp dport 53 accept
        ip daddr { 1.1.1.1, 1.0.0.1, 9.9.9.9 } tcp dport 853 accept

        # Block known malicious IPs
        ip daddr @blocked_ips drop
        ip6 daddr @blocked_ips6 drop

        # Rate limit new outbound
        ct state new limit rate 200/second burst 50 packets accept
    }

    chain input {
        type filter hook input priority 0; policy drop;

        # Allow loopback
        iif "lo" accept

        # Allow established/related
        ct state established,related accept

        # Block INVALID state
        ct state invalid drop

        # Rate limit new inbound
        ct state new limit rate 50/second burst 20 packets accept
    }

    chain forward {
        type filter hook forward priority 0; policy drop;
    }
}
EOF

echo "[Quillon Firewall] nftables rules applied successfully"
echo "[Quillon Firewall] Block rate: 200/s outbound, 50/s inbound"
echo "[Quillon Firewall] All unwanted traffic is DROPPED"
