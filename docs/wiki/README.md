# Wiki 一键发布

GitHub 新策略:wiki 的 git 仓库在**网页上创建第一个页面之前不存在**。
所以发布分两步:

## 1. 初始化(只需一次,需要 GitHub 登录)

浏览器打开:

```
https://github.com/GreenChennai/MagpieBridge/wiki
```

点 **"Create the first page"**,标题随便填(如 `Home`),内容随便,点 **Save page**。
这一步只是让 GitHub 把 `.wiki.git` 仓库建出来。

## 2. 推送全部页面(仓库根目录执行)

```bash
git clone git@github.com:GreenChennai/MagpieBridge.wiki.git _wiki
cp docs/wiki/*.md _wiki/
cd _wiki
git add -A
git commit -m "docs: 鹊桥 MagpieBridge 完整 Wiki"
git push
```

推送完成后访问 https://github.com/GreenChennai/MagpieBridge/wiki 即可,
侧栏导航(`_Sidebar.md`)自动生效。

页面源文件与仓库内 `docs/GUIDE.md`、`docs/adr/` 内容一致;
修改文档后请同步更新 `docs/wiki/` 并重推。
