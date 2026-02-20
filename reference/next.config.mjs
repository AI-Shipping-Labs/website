/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "export",
  typescript: {
    ignoreBuildErrors: true,
  },
  images: {
    unoptimized: true,
  },
  // If deploying to https://<user>.github.io/<repo>/, set basePath to `/<repo>`
  // basePath: "/ai-community-landing-page",
  // assetPrefix: "/ai-community-landing-page/",
}

export default nextConfig
