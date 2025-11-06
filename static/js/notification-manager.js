class NotificationManager {
  constructor() {
    this.isSupported = 'serviceWorker' in navigator && 'PushManager' in window;
  }

  async requestPermission() {
    if (!this.isSupported) {
      console.log('Push notifications not supported');
      return false;
    }

    try {
      const permission = await Notification.requestPermission();
      return permission === 'granted';
    } catch (error) {
      console.error('Permission request failed:', error);
      return false;
    }
  }

  async registerServiceWorker() {
    try {
      const registration = await navigator.serviceWorker.register('/static/service-worker.js');
      console.log('Service Worker registered:', registration);
      return registration;
    } catch (error) {
      console.error('Service Worker registration failed:', error);
      return null;
    }
  }

  async subscribeToNotifications() {
    if (!this.isSupported) return false;

    try {

      // Check if VAPID key exists
      const vapidElement = document.querySelector('meta   [name="vapid-public-key"]');
      const vapidKey = vapidElement?.content;
    
      if (!vapidKey) {
        console.error('VAPID public key not found in meta tag');
        alert('Push notifications not configured. Please check server settings.');
        return false;
      }

      // Request permission first
      const hasPermission = await this.requestPermission();
      if (!hasPermission) {
        console.log('User denied notification permission');
        return false;
      }

      // Register service worker
      const registration = await this.registerServiceWorker();
      if (!registration) return false;

      // Subscribe to push
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: this.urlBase64ToUint8Array(
          document.querySelector('meta[name="vapid-public-key"]')?.content || ''
        )
      });

      // Send subscription to server
      const response = await fetch('/api/notifications/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(subscription)
      });

      if (response.ok) {
        console.log('Successfully subscribed to notifications');
        return true;
      }
      return false;
    } catch (error) {
      console.error('Subscription failed:', error);
      return false;
    }
  }

  async unsubscribeFromNotifications() {
    if (!this.isSupported) return false;

    try {
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription();
      
      if (subscription) {
        await subscription.unsubscribe();
        // Notify server
        await fetch('/api/notifications/unsubscribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ endpoint: subscription.endpoint })
        });
        console.log('Unsubscribed from notifications');
        return true;
      }
      return false;
    } catch (error) {
      console.error('Unsubscription failed:', error);
      return false;
    }
  }

  async getSubscriptionStatus() {
    if (!this.isSupported) return null;

    try {
      const registration = await navigator.serviceWorker.ready;
      return await registration.pushManager.getSubscription();
    } catch (error) {
      console.error('Failed to get subscription status:', error);
      return null;
    }
  }

  urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding)
      .replace(/\-/g, '+')
      .replace(/_/g, '/');

    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);

    for (let i = 0; i < rawData.length; ++i) {
      outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  window.notificationManager = new NotificationManager();
});
