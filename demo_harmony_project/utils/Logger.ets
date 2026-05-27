export function logEvent(eventName: string): string {
  return `event:${eventName}`
}

export function logError(errorMessage: string): string {
  return `error:${errorMessage}`
}
