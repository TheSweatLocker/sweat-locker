ALTER TABLE public.signal_registry
    ADD COLUMN IF NOT EXISTS pick_control_pct NUMERIC;

ALTER TABLE public.signal_registry
    ADD COLUMN IF NOT EXISTS discrimination_pp NUMERIC;

ALTER TABLE public.signal_registry
    ADD COLUMN IF NOT EXISTS disagree_n INTEGER;
