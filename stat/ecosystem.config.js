module.exports = {
  apps: [
    {
      name: "pcss-stat",
      script: "node_modules/next/dist/bin/next",
      args: "start -p 3001",
      cwd: "./",         // 프로젝트 루트
      env: {
        NODE_ENV: "production"
      }
    }
  ]
};
