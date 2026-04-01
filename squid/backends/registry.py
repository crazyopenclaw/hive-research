"""Backend registry — maps backend_type string to backend class."""

REGISTRY = {
    "sandbox_python": "squid.backends.sandbox_python.executor:SandboxPythonBackend",
    "gpu_training": "squid.backends.gpu_training.executor:GPUTrainingBackend",
    "bio_pipeline": "squid.backends.bio_pipeline.executor:BioBackend",
    "simulation": "squid.backends.simulation.executor:SimulationBackend",
}
