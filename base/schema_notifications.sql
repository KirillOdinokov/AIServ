-- Уведомления веб-интерфейса: недоступность сайтов/серверов, нагрузка, TTFB.
CREATE TABLE IF NOT EXISTS notifications (
    id BIGSERIAL PRIMARY KEY,
    dedup_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'warning',
    subject TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    read_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS notifications_active_key
    ON notifications (dedup_key) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS notifications_created_idx
    ON notifications (created_at DESC);

-- Отметка, что отправителю прайса уже отправлен автоответ.
ALTER TABLE email_attachments ADD COLUMN IF NOT EXISTS notified_at TIMESTAMPTZ;
