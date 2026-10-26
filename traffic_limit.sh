#!/bin/bash
# Ограничение скорости сети сервера в рабочее время (5 Мбит/с в каждую сторону).
# Рабочее время: будни 07:00–19:00 МСК. Выходные и ночь — без ограничения.

IFACE="enp7s0"
RATE="5mbit"
BURST="32kbit"

is_working_time() {
    local now_day now_hour
    now_day=$(date +%u)     # 1..7 (1=пн, 7=вс)
    now_hour=$(date +%H)

    # суббота/воскресенье — без лимита
    [ "$now_day" -ge 6 ] && return 1
    # будни 07:00–19:00 — лимит
    [ "$now_hour" -ge 7 ] && [ "$now_hour" -lt 19 ] && return 0
    return 1
}

apply_limit() {
    # исходящий трафик (egress)
    tc qdisc del dev "$IFACE" root 2>/dev/null
    tc qdisc add dev "$IFACE" root handle 1: htb default 10
    tc class add dev "$IFACE" parent 1: classid 1:10 htb rate "$RATE" ceil "$RATE" burst "$BURST"

    # входящий трафик (ingress)
    tc qdisc del dev "$IFACE" ingress 2>/dev/null
    tc qdisc add dev "$IFACE" handle ffff: ingress
    tc filter add dev "$IFACE" parent ffff: protocol ip prio 1 \
        u32 match u32 0 0 police rate "$RATE" burst "$BURST" drop
}

remove_limit() {
    tc qdisc del dev "$IFACE" root 2>/dev/null
    tc qdisc del dev "$IFACE" ingress 2>/dev/null
}

if is_working_time; then
    apply_limit
    echo "[$(date)] Рабочее время — лимит ${RATE} применён"
else
    remove_limit
    echo "[$(date)] Не рабочее время — лимит снят"
fi
