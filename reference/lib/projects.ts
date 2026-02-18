import fs from "fs"
import path from "path"
import matter from "gray-matter"
import { remark } from "remark"
import html from "remark-html"

const projectsDirectory = path.join(process.cwd(), "content/projects")

export interface Project {
  slug: string
  title: string
  description: string
  date: string
  author?: string
  tags?: string[]
  readingTime?: string
  contentHtml: string
  difficulty?: "beginner" | "intermediate" | "advanced"
  estimatedTime?: string
}

export interface ProjectMeta {
  slug: string
  title: string
  description: string
  date: string
  author?: string
  tags?: string[]
  readingTime?: string
  difficulty?: "beginner" | "intermediate" | "advanced"
  estimatedTime?: string
}

function calculateReadingTime(content: string): string {
  const wordsPerMinute = 200
  const words = content.split(/\s+/).length
  const minutes = Math.ceil(words / wordsPerMinute)
  return `${minutes} min read`
}

export async function getAllProjects(): Promise<ProjectMeta[]> {
  if (!fs.existsSync(projectsDirectory)) {
    return []
  }

  const fileNames = fs.readdirSync(projectsDirectory)
  const allProjectsData = fileNames
    .filter((fileName) => fileName.endsWith(".md"))
    .map((fileName) => {
      const slug = fileName.replace(/\.md$/, "")
      const fullPath = path.join(projectsDirectory, fileName)
      const fileContents = fs.readFileSync(fullPath, "utf8")
      const { data, content } = matter(fileContents)

      return {
        slug,
        title: data.title || slug,
        description: data.description || "",
        date: data.date || new Date().toISOString(),
        author: data.author,
        tags: data.tags || [],
        readingTime: calculateReadingTime(content),
        difficulty: data.difficulty,
        estimatedTime: data.estimatedTime,
      }
    })

  return allProjectsData.sort((a, b) => (a.date < b.date ? 1 : -1))
}

export async function getProjectBySlug(slug: string): Promise<Project | null> {
  if (!fs.existsSync(projectsDirectory)) {
    return null
  }

  const fullPath = path.join(projectsDirectory, `${slug}.md`)
  
  if (!fs.existsSync(fullPath)) {
    return null
  }

  const fileContents = fs.readFileSync(fullPath, "utf8")
  const { data, content } = matter(fileContents)

  const processedContent = await remark().use(html).process(content)
  const contentHtml = processedContent.toString()

  return {
    slug,
    title: data.title || slug,
    description: data.description || "",
    date: data.date || new Date().toISOString(),
    author: data.author,
    tags: data.tags || [],
    readingTime: calculateReadingTime(content),
    contentHtml,
    difficulty: data.difficulty,
    estimatedTime: data.estimatedTime,
  }
}
