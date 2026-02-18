"use client"

interface TimestampLinkProps {
  time: string
  title: string
  description?: string
  videoId: string
  isYouTube: boolean
  onSeek?: (seconds: number) => void
}

export function TimestampLink({ time, title, description, videoId, isYouTube, onSeek }: TimestampLinkProps) {
  const timeToSeconds = (timeStr: string): number => {
    const parts = timeStr.split(":").map(Number)
    if (parts.length === 2) {
      // MM:SS format
      return parts[0] * 60 + parts[1]
    } else if (parts.length === 3) {
      // HH:MM:SS format
      return parts[0] * 3600 + parts[1] * 60 + parts[2]
    }
    return 0
  }

  const handleClick = (e: React.MouseEvent) => {
    e.preventDefault()
    if (!isYouTube || !onSeek) return
    
    const seconds = timeToSeconds(time)
    onSeek(seconds)
  }

  const seconds = timeToSeconds(time)
  const isClickable = isYouTube && onSeek

  if (isClickable) {
    return (
      <a
        href="#"
        onClick={handleClick}
        data-time={seconds}
        className="block rounded-lg border border-border bg-card p-4 transition-colors hover:border-accent/50 hover:bg-card/80 cursor-pointer"
      >
        <div className="flex items-start gap-4">
          <span className="flex-shrink-0 rounded-full bg-accent/20 px-3 py-1 font-mono text-sm font-medium text-accent">
            {time}
          </span>
          <div className="flex-1">
            <span className="font-semibold text-foreground hover:text-accent transition-colors">
              {title}
            </span>
            {description && (
              <p className="mt-1 text-sm text-muted-foreground">
                {description}
              </p>
            )}
          </div>
        </div>
      </a>
    )
  }

  return (
    <div className="rounded-lg border border-border bg-card p-4 transition-colors hover:border-accent/50">
      <div className="flex items-start gap-4">
        <span className="flex-shrink-0 rounded-full bg-accent/20 px-3 py-1 font-mono text-sm font-medium text-accent">
          {time}
        </span>
        <div className="flex-1">
          <h3 className="font-semibold text-foreground">
            {title}
          </h3>
          {description && (
            <p className="mt-1 text-sm text-muted-foreground">
              {description}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
