from dataclasses import dataclass


@dataclass
class VADConfig:

    # ====================================================
    # Existing VADService requirements
    # ====================================================

    sample_rate: int = 16000
    channels: int = 1

    vad_aggressiveness: int = 2
    vad_silence_duration: float = 1.8
    vad_min_speech_duration: float = 0.4
    max_recording_duration: float = 20.0

    silero_confidence_threshold: float = 0.50
    use_silero: bool = True

    node_ws_uri: str = (
        "ws://localhost:8765"
    )


    # ====================================================
    # Existing runtime constants
    # ====================================================

    frame_duration: int = 30


    # silence / pause thresholds

    silence_short: float = 0.65
    silence_medium: float = 1.25
    silence_long: float = 2.10


    # deferred finalize

    defer_window: float = 0.22


    # burst grouping

    burst_gap: float = 0.35


    # interruption

    interrupt_rms: float = 1800


    # silero escalation

    silero_ambig_low: float = 0.35
    silero_ambig_high: float = 0.70


    # conversational runtime

    rms_threshold: int = 900

    zcr_min: float = 0.015
    zcr_max: float = 0.28


    # momentum

    momentum_onset_frames: int = 3

    momentum_rise: float = 0.28
    momentum_decay: float = 0.92
    momentum_drop: float = 0.68

    momentum_max: float = 6.0