class LocationHandler {
    constructor() {
        this.bestPosition = null;
        this.bestAccuracy = Infinity;
        this.attempts = 0;
        this.watchId = null;
        this.statusCallback = null;
    }

    /**
     * Get high-accuracy location with progressive improvement
     * @param {Object} options - Configuration options
     * @returns {Promise<Position>} - Best available position
     */
    async getAccurateLocation(options = {}) {
        const config = {
            maxWaitTime: options.maxWaitTime || 15000, // 15 seconds max wait
            acceptableAccuracy: options.acceptableAccuracy || 50, // Accept if < 50m
            minimumAccuracy: options.minimumAccuracy || 500, // Reject if > 500m
            maxAttempts: options.maxAttempts || 10,
            onStatusUpdate: options.onStatusUpdate || null
        };

        this.statusCallback = config.onStatusUpdate;
        this.reset();

        return new Promise((resolve, reject) => {
            const startTime = Date.now();

            // Update status
            this.updateStatus('Activating GPS...', 'info');

            // Start watching position
            this.watchId = navigator.geolocation.watchPosition(
                (position) => {
                    this.attempts++;
                    const accuracy = position.coords.accuracy;
                    const elapsed = Date.now() - startTime;

                    console.log(`[GPS] Attempt ${this.attempts}: ${Math.round(accuracy)}m accuracy`);

                    // Track best position
                    if (accuracy < this.bestAccuracy) {
                        this.bestAccuracy = accuracy;
                        this.bestPosition = position;
                        
                        this.updateStatus(
                            `Getting location... ${Math.round(accuracy)}m accuracy`,
                            this.getAccuracyLevel(accuracy).type
                        );
                    }

                    // SUCCESS: Got excellent accuracy
                    if (accuracy < config.acceptableAccuracy) {
                        this.cleanup();
                        this.updateStatus(`✓ Location acquired (${Math.round(accuracy)}m)`, 'success');
                        resolve(this.bestPosition);
                        return;
                    }

                    // TIMEOUT: Return best position we have
                    if (elapsed > config.maxWaitTime) {
                        this.cleanup();

                        if (this.bestAccuracy < config.minimumAccuracy) {
                            this.updateStatus(
                                `Location acquired (${Math.round(this.bestAccuracy)}m - approximate)`,
                                'warning'
                            );
                            resolve(this.bestPosition);
                        } else {
                            this.updateStatus('Location accuracy too low', 'error');
                            reject(new Error(
                                `Location too inaccurate: ${Math.round(this.bestAccuracy)}m. ` +
                                `Please move outdoors and try again.`
                            ));
                        }
                        return;
                    }

                    // MAX ATTEMPTS: Return what we have if reasonable
                    if (this.attempts >= config.maxAttempts) {
                        this.cleanup();

                        if (this.bestAccuracy < config.minimumAccuracy) {
                            this.updateStatus(`Location acquired (${Math.round(this.bestAccuracy)}m)`, 'warning');
                            resolve(this.bestPosition);
                        } else {
                            this.updateStatus('Could not get accurate location', 'error');
                            reject(new Error('GPS accuracy insufficient'));
                        }
                    }
                },
                (error) => {
                    this.cleanup();
                    this.updateStatus('Location access denied', 'error');
                    reject(this.handleGeolocationError(error));
                },
                {
                    enableHighAccuracy: true,
                    timeout: 10000,
                    maximumAge: 0 // Never use cached location
                }
            );
        });
    }

    /**
     * Get location quality assessment
     */
    getAccuracyLevel(accuracy) {
        if (accuracy < 20) {
            return { level: 'excellent', color: '#22c55e', type: 'success', text: 'Excellent' };
        } else if (accuracy < 50) {
            return { level: 'good', color: '#84cc16', type: 'success', text: 'Good' };
        } else if (accuracy < 100) {
            return { level: 'fair', color: '#f59e0b', type: 'warning', text: 'Fair' };
        } else if (accuracy < 200) {
            return { level: 'poor', color: '#ef4444', type: 'warning', text: 'Poor' };
        } else {
            return { level: 'very_poor', color: '#991b1b', type: 'error', text: 'Very Poor' };
        }
    }

    /**
     * Handle geolocation errors with user-friendly messages
     */
    handleGeolocationError(error) {
        const errorMessages = {
            1: 'Location access denied. Please enable location permissions in your browser settings.',
            2: 'Location unavailable. Please check your GPS/internet connection.',
            3: 'Location request timed out. Please try again.'
        };

        return new Error(errorMessages[error.code] || 'Unknown location error');
    }

    /**
     * Quick location check (for status display)
     */
    async quickCheck() {
        return new Promise((resolve, reject) => {
            navigator.geolocation.getCurrentPosition(
                (position) => resolve(position),
                (error) => reject(error),
                { enableHighAccuracy: false, timeout: 5000, maximumAge: 30000 }
            );
        });
    }

    /**
     * Update status callback
     */
    updateStatus(message, type) {
        if (this.statusCallback) {
            this.statusCallback(message, type);
        }
    }

    /**
     * Clean up watch
     */
    cleanup() {
        if (this.watchId) {
            navigator.geolocation.clearWatch(this.watchId);
            this.watchId = null;
        }
    }

    /**
     * Reset state
     */
    reset() {
        this.bestPosition = null;
        this.bestAccuracy = Infinity;
        this.attempts = 0;
        this.cleanup();
    }
}

// Export for use in other scripts
window.LocationHandler = LocationHandler;