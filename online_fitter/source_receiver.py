"""Protected fitter input admission. No hardware access.

The observation callable must supply verified state from trusted
EPICS/DAQ adapters. Incoming JSON never supplies switch identity,
communication status, or freshness measurements.

A post-switch acquisition barrier is mandatory before accepting data.
"""

from .source_gate import evaluate_source


class SourceAwareProcessor:
    def __init__(self, observe, fit=None):
        if not callable(observe):
            raise TypeError("observe must be callable")
        self.observe = observe
        self.fit = fit

    @staticmethod
    def reject(reason):
        return {"ok": False, "error": "NMR source rejected: " + str(reason)}

    def __call__(self, state, msg, trusted_source):
        if not isinstance(msg, dict):
            return self.reject("invalid sweep message")

        try:
            observation = self.observe()
        except Exception:
            return self.reject("trusted source observation unavailable")

        if not isinstance(observation, dict):
            return self.reject("missing trusted source observation")

        if observation.get("post_switch_ready") is not True:
            return self.reject("post-switch acquisition barrier not verified")

        decision = evaluate_source(
            selected_source=observation.get("selected_source"),
            transport_source=trusted_source,
            payload_source=msg.get("source"),
            nmr_mask=observation.get("nmr_mask"),
            nmr_comm=observation.get("nmr_comm"),
            route_age_s=observation.get("route_age_s"),
            data_age_s=observation.get("data_age_s"),
            stream_connected=observation.get("stream_connected"),
        )

        if not decision.accepted:
            return self.reject(decision.reason)

        if self.fit is not None:
            return self.fit(state, msg)

        from .server import _handle_sweep
        return _handle_sweep(state, msg)
