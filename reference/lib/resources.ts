import fs from "fs"
import path from "path"
import matter from "gray-matter"

const resourcesDirectory = path.join(process.cwd(), "content/resources")

export interface Resource {
  slug: string
  title: string
  description: string
  date: string
  tags?: string[]
  level?: string
  googleEmbedUrl?: string
  youtubeUrl?: string
  timestamps?: Array<{
    time: string
    title: string
    description?: string
  }>
  materials?: Array<{
    title: string
    url: string
    type?: "slides" | "code" | "article" | "other"
  }>
  coreTools?: string[]
  learningObjectives?: string[]
  outcome?: string
  relatedCourse?: string
}

export interface ResourceMeta {
  slug: string
  title: string
  description: string
  date: string
  tags?: string[]
}

export async function getAllResources(): Promise<ResourceMeta[]> {
  if (!fs.existsSync(resourcesDirectory)) {
    return []
  }

  const fileNames = fs.readdirSync(resourcesDirectory)
  const allResourcesData = fileNames
    .filter((fileName) => fileName.endsWith(".md"))
    .map((fileName) => {
      const slug = fileName.replace(/\.md$/, "")
      const fullPath = path.join(resourcesDirectory, fileName)
      const fileContents = fs.readFileSync(fullPath, "utf8")
      const { data } = matter(fileContents)

      return {
        slug,
        title: data.title || slug,
        description: data.description || "",
        date: data.date || new Date().toISOString(),
        tags: data.tags || [],
      }
    })

  return allResourcesData.sort((a, b) => (a.date < b.date ? 1 : -1))
}

export async function getResourceBySlug(slug: string): Promise<Resource | null> {
  if (!fs.existsSync(resourcesDirectory)) {
    return null
  }

  const fullPath = path.join(resourcesDirectory, `${slug}.md`)
  
  if (!fs.existsSync(fullPath)) {
    return null
  }

  const fileContents = fs.readFileSync(fullPath, "utf8")
  const { data } = matter(fileContents)

  return {
    slug,
    title: data.title || slug,
    description: data.description || "",
    date: data.date || new Date().toISOString(),
    tags: data.tags || [],
    level: data.level,
    googleEmbedUrl: data.googleEmbedUrl,
    youtubeUrl: data.youtubeUrl,
    timestamps: data.timestamps || [],
    materials: data.materials || [],
    coreTools: data.coreTools || [],
    learningObjectives: data.learningObjectives || [],
    outcome: data.outcome,
    relatedCourse: data.relatedCourse,
  }
}
