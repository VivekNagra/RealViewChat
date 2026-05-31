"""Asynchronous Vision-pipeline messaging (RabbitMQ).

A producer enqueues inspection jobs; a worker consumes them, runs the Vision
pipeline through the existing LLMClient seam, and persists the Property
aggregate via the shared atomic unit. Bounded retry + dead-letter give
robustness under at-least-once delivery.
"""
