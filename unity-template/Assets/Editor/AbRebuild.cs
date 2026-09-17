using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

public static class AbRebuild
{
    [MenuItem("ConversionCulib/Build Android AssetBundles")]
    public static void BuildAndroidMenu()
    {
        BuildAndroid();
    }

    [MenuItem("ConversionCulib/Assign Bundle Names From Mapping")]
    public static void AssignBundleNamesFromMappingMenu()
    {
        AssetDatabase.Refresh();
        AssignBundleNamesFromMapping();
        AssetDatabase.SaveAssets();
    }

    public static void BuildAndroid()
    {
        string[] args = Environment.GetCommandLineArgs();
        string mappingArg = GetArg(args, "-mapping");
        string output = GetArg(args, "-output");
        string androidTexture = GetArg(args, "-androidTexture");
        string compression = GetArg(args, "-compression");
        string astcBlockSize = GetArg(args, "-astcBlockSize");
        string maxTextureSize = GetArg(args, "-maxTextureSize");

        ApplyAndroidTexture(androidTexture);

        if (string.IsNullOrEmpty(output))
        {
            string projectRoot = Path.GetDirectoryName(Application.dataPath);
            output = Path.Combine(projectRoot, "Abs", "Android");
        }

        output = Path.GetFullPath(output);
        Directory.CreateDirectory(output);

        AssetDatabase.Refresh();

        AbFlatMapping mapping = LoadFlatMapping(mappingArg);
        if (mapping == null)
        {
            FailAndMaybeExit("mapping not found (ab_mapping_flat.json / -mapping)");
            return;
        }

        AssignResult result = AssignBundleNamesFromMapping(mapping);
        WriteMissing(result.missing);

        if (result.assigned == 0)
        {
            string reason = result.mappingAssetCount > 0
                ? "ZERO assets got a bundle name but mapping listed assets"
                : "no assets assigned to AssetBundles (empty mapping or UnityPy/manifest produced no assets)";
            FailAndMaybeExit(reason);
            return;
        }

        ApplyAndroidAssetCompression(androidTexture, astcBlockSize, maxTextureSize);
        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh();

        BuildAssetBundleOptions options = ParseCompression(compression) | BuildAssetBundleOptions.ForceRebuildAssetBundle;
        AssetBundleManifest manifest = BuildPipeline.BuildAssetBundles(output, options, BuildTarget.Android);
        if (manifest == null)
        {
            FailAndMaybeExit("BuildAssetBundles returned null (Android module missing or build failed)");
            return;
        }

        Debug.Log("AbRebuild: built Android AssetBundles at " + output + " assigned=" + result.assigned);
        // Success: return and let -quit exit 0. FailAndMaybeExit uses Exit(1).
    }

    public static void AssignBundleNamesFromMapping()
    {
        string mappingArg = GetArg(Environment.GetCommandLineArgs(), "-mapping");
        AbFlatMapping mapping = LoadFlatMapping(mappingArg);
        if (mapping == null)
        {
            Debug.LogError("AbRebuild: mapping not found");
            return;
        }

        AssignResult result = AssignBundleNamesFromMapping(mapping);
        WriteMissing(result.missing);
        Debug.Log("AbRebuild: assigned " + result.assigned + " assets, missing " + result.missing.Count);
    }

    static AssignResult AssignBundleNamesFromMapping(AbFlatMapping mapping)
    {
        AbFlatItem[] items = mapping.items ?? new AbFlatItem[0];
        AssetIndex index = BuildAssetIndex();
        ClearBundleNames(index);

        var assignedPaths = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        var missing = new List<AbFlatItem>();
        int mappingAssetCount = 0;
        int assigned = 0;

        for (int i = 0; i < items.Length; i++)
        {
            AbFlatItem item = items[i] ?? new AbFlatItem();
            bool hasIdentity = !string.IsNullOrEmpty(item.path) || !string.IsNullOrEmpty(item.name);
            if (!hasIdentity || string.IsNullOrEmpty(item.bundle))
            {
                if (hasIdentity)
                    missing.Add(item);
                continue;
            }

            if (string.Equals(item.type, "MonoScript", StringComparison.OrdinalIgnoreCase))
                continue;

            mappingAssetCount++;
            string resolved = ResolveAssetPath(item, index);
            if (string.IsNullOrEmpty(resolved) || IsForbiddenPath(resolved))
            {
                missing.Add(item);
                Debug.LogWarning(
                    "AbRebuild: missing asset bundle=" + item.bundle
                    + " name=" + item.name
                    + " type=" + item.type
                    + " path=" + item.path);
                continue;
            }

            AssetImporter importer = AssetImporter.GetAtPath(resolved);
            if (importer == null)
            {
                missing.Add(item);
                Debug.LogWarning("AbRebuild: no importer for " + resolved);
                continue;
            }

            string previous;
            if (assignedPaths.TryGetValue(resolved, out previous) && previous != item.bundle)
            {
                Debug.LogWarning("AbRebuild: asset already assigned to " + previous + ", overriding with " + item.bundle + " path=" + resolved);
            }

            importer.assetBundleName = item.bundle;
            assignedPaths[resolved] = item.bundle;
            assigned++;
        }

        IncludeDependencies(assignedPaths);
        AttachSpineFamily(assignedPaths, index);
        assigned = assignedPaths.Count;
        AssetDatabase.RemoveUnusedAssetBundleNames();
        Debug.Log("AbRebuild: assigned " + assigned + "/" + mappingAssetCount + " mapped assets");
        return new AssignResult
        {
            assigned = assigned,
            mappingAssetCount = mappingAssetCount,
            missing = missing
        };
    }

    static string ResolveAssetPath(AbFlatItem item, AssetIndex index)
    {
        // 0) 隔离转换：先在当前 bundle 自己的 Assets/Bundles/<bundle>/ 目录内精确匹配
        string scoped = FindInBundle(item, index);
        if (!string.IsNullOrEmpty(scoped))
            return scoped;

        string mappingPath = (item.path ?? "").Replace('\\', '/');

        // 1) exact project path as written in mapping
        string exact = FindExactPath(mappingPath, index);
        if (!string.IsNullOrEmpty(exact))
            return exact;

        // 2) suffix after stripping leading Assets/ and Ripped/
        string suffix = FindSuffixPath(mappingPath, index);
        if (!string.IsNullOrEmpty(suffix))
            return suffix;

        // 3) filename without extension + type/extension heuristic
        return FindNameTypePath(item, index);
    }

    static string FindInBundle(AbFlatItem item, AssetIndex index)
    {
        string bundle = item.bundle;
        if (string.IsNullOrEmpty(bundle))
            return null;

        string name = item.name;
        if (string.IsNullOrEmpty(name) && !string.IsNullOrEmpty(item.path))
            name = Path.GetFileNameWithoutExtension(item.path.Replace('\\', '/'));
        if (string.IsNullOrEmpty(name))
            return null;

        var candidates = new List<string>();
        for (int i = 0; i < index.paths.Count; i++)
        {
            string path = index.paths[i];
            string folder = BundleFolderOf(path);
            if (folder == null || !folder.Equals(bundle, StringComparison.OrdinalIgnoreCase))
                continue;
            string file = Path.GetFileNameWithoutExtension(path.Replace('\\', '/'));
            if (file.Equals(name, StringComparison.OrdinalIgnoreCase))
                candidates.Add(path);
        }

        if (candidates.Count == 0)
            return null;

        var typed = new List<string>();
        for (int i = 0; i < candidates.Count; i++)
        {
            if (TypeMatches(candidates[i], item.type))
                typed.Add(candidates[i]);
        }

        List<string> pool = typed.Count > 0 ? typed : candidates;
        return PickBest(pool, item.path, "bundle");
    }

    static string FindExactPath(string mappingPath, AssetIndex index)
    {
        if (string.IsNullOrEmpty(mappingPath))
            return null;

        string slash = mappingPath.Replace('\\', '/');
        string found;
        if (index.byExact.TryGetValue(slash, out found))
            return found;
        if (!StartsWithFolder(slash, "Assets") && index.byExact.TryGetValue("Assets/" + slash.TrimStart('/'), out found))
            return found;
        return null;
    }

    static string FindSuffixPath(string mappingPath, AssetIndex index)
    {
        if (string.IsNullOrEmpty(mappingPath))
            return null;

        string mapNorm = StripKnownPrefixes(mappingPath);
        if (string.IsNullOrEmpty(mapNorm))
            return null;

        var matches = new List<string>();
        List<string> exactNorm;
        if (index.byNorm.TryGetValue(mapNorm, out exactNorm))
            matches.AddRange(exactNorm);

        foreach (var pair in index.byNorm)
        {
            if (pair.Key.Equals(mapNorm, StringComparison.OrdinalIgnoreCase))
                continue;
            if (pair.Key.EndsWith("/" + mapNorm, StringComparison.OrdinalIgnoreCase))
                matches.AddRange(pair.Value);
        }

        return PickBest(matches, mappingPath, "suffix");
    }

    static string FindNameTypePath(AbFlatItem item, AssetIndex index)
    {
        string name = item.name;
        if (string.IsNullOrEmpty(name) && !string.IsNullOrEmpty(item.path))
            name = Path.GetFileNameWithoutExtension(item.path.Replace('\\', '/'));
        if (string.IsNullOrEmpty(name))
            return null;

        List<string> named;
        if (!index.byFile.TryGetValue(name, out named) || named == null || named.Count == 0)
            return null;

        var typed = new List<string>();
        for (int i = 0; i < named.Count; i++)
        {
            if (TypeMatches(named[i], item.type))
                typed.Add(named[i]);
        }

        if (typed.Count == 0)
            return null;
        return PickBest(typed, item.path, "name+type");
    }

    static string PickBest(List<string> matches, string mappingPath, string rule)
    {
        if (matches == null || matches.Count == 0)
            return null;

        var unique = matches.Distinct(StringComparer.OrdinalIgnoreCase).ToList();
        if (unique.Count == 1)
            return unique[0];

        string mapNorm = StripKnownPrefixes(mappingPath ?? "");
        var exact = unique.Where(m => StripKnownPrefixes(m).Equals(mapNorm, StringComparison.OrdinalIgnoreCase)).ToList();
        if (exact.Count == 1)
            return exact[0];

        unique.Sort((a, b) =>
        {
            int len = StripKnownPrefixes(a).Length.CompareTo(StripKnownPrefixes(b).Length);
            if (len != 0)
                return len;
            return string.Compare(a, b, StringComparison.OrdinalIgnoreCase);
        });

        Debug.LogWarning("AbRebuild: ambiguous " + rule + " match for path=" + mappingPath + " using " + unique[0] + " of " + unique.Count);
        return unique[0];
    }

    static bool TypeMatches(string assetPath, string type)
    {
        if (string.IsNullOrEmpty(type))
            return true;

        Type unityType = AssetDatabase.GetMainAssetTypeAtPath(assetPath);
        if (unityType != null)
        {
            if (unityType.Name.Equals(type, StringComparison.OrdinalIgnoreCase))
                return true;
            if (type.Equals("Texture", StringComparison.OrdinalIgnoreCase) && typeof(Texture).IsAssignableFrom(unityType))
                return true;
            if (type.Equals("Sprite", StringComparison.OrdinalIgnoreCase) && (unityType.Name == "Sprite" || unityType.Name == "Texture2D"))
                return true;
            if (type.Equals("Prefab", StringComparison.OrdinalIgnoreCase) && unityType.Name == "GameObject")
                return true;
        }

        string ext = Path.GetExtension(assetPath);
        string[] exts = ExtensionsForType(type);
        for (int i = 0; i < exts.Length; i++)
        {
            if (ext.Equals(exts[i], StringComparison.OrdinalIgnoreCase))
                return true;
        }

        return false;
    }

    static string[] ExtensionsForType(string type)
    {
        switch (type)
        {
            case "GameObject":
            case "Prefab":
            case "PrefabInstance":
                return new[] { ".prefab" };
            case "Texture":
            case "Texture2D":
            case "Texture3D":
            case "Cubemap":
            case "Sprite":
                return new[] { ".png", ".jpg", ".jpeg", ".tga", ".psd", ".tif", ".tiff", ".exr", ".hdr", ".bmp", ".gif", ".webp", ".dds" };
            case "Material":
                return new[] { ".mat" };
            case "AudioClip":
                return new[] { ".wav", ".mp3", ".ogg", ".aif", ".aiff" };
            case "AnimationClip":
            case "Animation":
                return new[] { ".anim" };
            case "AnimatorController":
                return new[] { ".controller" };
            case "AnimatorOverrideController":
                return new[] { ".overrideController" };
            case "Shader":
                return new[] { ".shader" };
            case "ComputeShader":
                return new[] { ".compute" };
            case "Font":
                return new[] { ".ttf", ".otf", ".fontsettings" };
            case "TextAsset":
                return new[] { ".txt", ".bytes", ".json", ".xml", ".csv", ".atlas", ".skel" };
            case "Mesh":
                return new[] { ".fbx", ".obj", ".mesh", ".dae" };
            case "Scene":
            case "SceneAsset":
                return new[] { ".unity" };
            case "MonoBehaviour":
            case "ScriptableObject":
                return new[] { ".asset" };
            case "PhysicMaterial":
            case "PhysicsMaterial":
                return new[] { ".physicMaterial", ".physicsMaterial" };
            case "VideoClip":
                return new[] { ".mp4", ".mov", ".webm", ".avi" };
            case "SpriteAtlas":
                return new[] { ".spriteatlas" };
            case "RenderTexture":
                return new[] { ".renderTexture" };
            case "AvatarMask":
                return new[] { ".mask" };
            default:
                return new string[0];
        }
    }

    static AssetIndex BuildAssetIndex()
    {
        var index = new AssetIndex();
        string[] paths = AssetDatabase.GetAllAssetPaths();
        for (int i = 0; i < paths.Length; i++)
        {
            string path = paths[i];
            if (IsForbiddenPath(path) || AssetDatabase.IsValidFolder(path))
                continue;

            index.paths.Add(path);
            string slash = path.Replace('\\', '/');
            if (!index.byExact.ContainsKey(slash))
                index.byExact.Add(slash, path);

            string norm = StripKnownPrefixes(slash);
            List<string> list;
            if (!index.byNorm.TryGetValue(norm, out list))
            {
                list = new List<string>();
                index.byNorm.Add(norm, list);
            }
            list.Add(path);

            string file = Path.GetFileNameWithoutExtension(slash);
            if (string.IsNullOrEmpty(file))
                continue;
            if (!index.byFile.TryGetValue(file, out list))
            {
                list = new List<string>();
                index.byFile.Add(file, list);
            }
            list.Add(path);
        }

        return index;
    }

    static void ClearBundleNames(AssetIndex index)
    {
        for (int i = 0; i < index.paths.Count; i++)
        {
            AssetImporter importer = AssetImporter.GetAtPath(index.paths[i]);
            if (importer == null)
                continue;
            if (!string.IsNullOrEmpty(importer.assetBundleName) || !string.IsNullOrEmpty(importer.assetBundleVariant))
            {
                importer.assetBundleName = "";
                importer.assetBundleVariant = "";
            }
        }

        AssetDatabase.RemoveUnusedAssetBundleNames();
    }

    static bool IsForbiddenPath(string assetPath)
    {
        if (string.IsNullOrEmpty(assetPath))
            return true;

        string p = assetPath.Replace('\\', '/');
        if (p.Equals("ProjectSettings", StringComparison.OrdinalIgnoreCase)
            || p.StartsWith("ProjectSettings/", StringComparison.OrdinalIgnoreCase))
            return true;

        string ext = Path.GetExtension(p);
        if (ext.Equals(".cs", StringComparison.OrdinalIgnoreCase) || ext.Equals(".dll", StringComparison.OrdinalIgnoreCase))
            return true;

        string[] parts = p.Split('/');
        for (int i = 0; i < parts.Length; i++)
        {
            if (parts[i].Equals("Editor", StringComparison.OrdinalIgnoreCase))
                return true;
        }

        return false;
    }

    static string StripKnownPrefixes(string path)
    {
        string p = (path ?? "").Replace('\\', '/').Trim('/');
        bool stripped = true;
        while (stripped && p.Length > 0)
        {
            stripped = false;
            if (StartsWithFolder(p, "Assets"))
            {
                p = p.Substring("Assets".Length).Trim('/');
                stripped = true;
            }
            else if (StartsWithFolder(p, "Ripped"))
            {
                p = p.Substring("Ripped".Length).Trim('/');
                stripped = true;
            }
        }

        return p;
    }

    static bool StartsWithFolder(string path, string folder)
    {
        if (path.Equals(folder, StringComparison.OrdinalIgnoreCase))
            return true;
        return path.StartsWith(folder + "/", StringComparison.OrdinalIgnoreCase);
    }

    static string BundleFolderOf(string assetPath)
    {
        string p = (assetPath ?? "").Replace('\\', '/');
        int idx = p.IndexOf("/Bundles/", StringComparison.OrdinalIgnoreCase);
        if (idx < 0)
            return null;
        string rest = p.Substring(idx + "/Bundles/".Length);
        int slash = rest.IndexOf('/');
        if (slash < 0)
            return null;
        return rest.Substring(0, slash);
    }

    static AbFlatMapping LoadFlatMapping(string mappingArg)
    {
        var candidates = new List<string>();
        if (!string.IsNullOrEmpty(mappingArg))
        {
            candidates.Add(mappingArg);
            string dir = Path.GetDirectoryName(mappingArg);
            if (!string.IsNullOrEmpty(dir))
                candidates.Add(Path.Combine(dir, "ab_mapping_flat.json"));
        }

        string editor = Path.Combine(Application.dataPath, "Editor");
        candidates.Add(Path.Combine(editor, "ab_mapping_flat.json"));
        candidates.Add(Path.Combine(editor, "ab_mapping.json"));

        for (int i = 0; i < candidates.Count; i++)
        {
            string candidate = candidates[i];
            if (string.IsNullOrEmpty(candidate) || !File.Exists(candidate))
                continue;

            string text = File.ReadAllText(candidate);
            if (text.IndexOf("\"items\"", StringComparison.Ordinal) < 0)
                continue;

            AbFlatMapping loaded = JsonUtility.FromJson<AbFlatMapping>(text);
            if (loaded == null)
                continue;
            if (loaded.items == null)
                loaded.items = new AbFlatItem[0];
            Debug.Log("AbRebuild: loaded mapping " + candidate + " items=" + loaded.items.Length);
            return loaded;
        }

        return null;
    }

    static void WriteMissing(List<AbFlatItem> missing)
    {
        string path = Path.Combine(Application.dataPath, "Editor", "ab_missing.json");
        var wrapper = new AbFlatMapping();
        wrapper.items = missing == null ? new AbFlatItem[0] : missing.ToArray();
        File.WriteAllText(path, JsonUtility.ToJson(wrapper, true));
        if (wrapper.items.Length > 0)
            Debug.LogWarning("AbRebuild: wrote " + wrapper.items.Length + " missing assets to " + path);
    }

    static void AssignPathToBundle(string assetPath, string bundle, Dictionary<string, string> assignedPaths)
    {
        if (string.IsNullOrEmpty(assetPath) || string.IsNullOrEmpty(bundle))
            return;
        if (IsForbiddenPath(assetPath) || AssetDatabase.IsValidFolder(assetPath))
            return;
        if (assetPath.StartsWith("Packages/", StringComparison.OrdinalIgnoreCase)
            || assetPath.StartsWith("Resources/", StringComparison.OrdinalIgnoreCase))
            return;

        AssetImporter importer = AssetImporter.GetAtPath(assetPath);
        if (importer == null)
            return;

        string previous;
        if (assignedPaths.TryGetValue(assetPath, out previous) && previous == bundle)
            return;

        importer.assetBundleName = bundle;
        assignedPaths[assetPath] = bundle;
    }

    static void IncludeDependencies(Dictionary<string, string> assignedPaths)
    {
        var snapshot = assignedPaths.ToList();
        for (int i = 0; i < snapshot.Count; i++)
        {
            string root = snapshot[i].Key;
            string bundle = snapshot[i].Value;
            string[] deps = AssetDatabase.GetDependencies(root, true);
            for (int d = 0; d < deps.Length; d++)
                AssignPathToBundle(deps[d], bundle, assignedPaths);
        }
    }

    static string SpineStem(string assetPath)
    {
        string file = Path.GetFileName(assetPath ?? "");
        if (file.EndsWith(".atlas.txt", StringComparison.OrdinalIgnoreCase))
            return file.Substring(0, file.Length - ".atlas.txt".Length);
        if (file.EndsWith(".skel.bytes", StringComparison.OrdinalIgnoreCase))
            return file.Substring(0, file.Length - ".skel.bytes".Length);

        string stem = Path.GetFileNameWithoutExtension(file);
        string[] suffixes = { "_SkeletonData", "_Atlas", "_Material", "_Controller" };
        for (int i = 0; i < suffixes.Length; i++)
        {
            if (stem.EndsWith(suffixes[i], StringComparison.OrdinalIgnoreCase))
                return stem.Substring(0, stem.Length - suffixes[i].Length);
        }

        if (stem.EndsWith(".atlas", StringComparison.OrdinalIgnoreCase))
            return stem.Substring(0, stem.Length - ".atlas".Length);
        if (stem.EndsWith(".skel", StringComparison.OrdinalIgnoreCase))
            return stem.Substring(0, stem.Length - ".skel".Length);
        return stem;
    }

    static bool LooksLikeSpineAsset(string assetPath)
    {
        string file = Path.GetFileName(assetPath ?? "");
        string lower = file.ToLowerInvariant();
        if (lower.EndsWith(".atlas.txt") || lower.EndsWith(".atlas") || lower.EndsWith(".skel") || lower.EndsWith(".skel.bytes"))
            return true;
        string stem = Path.GetFileNameWithoutExtension(file);
        return stem.EndsWith("_SkeletonData", StringComparison.OrdinalIgnoreCase)
            || stem.EndsWith("_Atlas", StringComparison.OrdinalIgnoreCase)
            || lower.Contains("spine");
    }

    static void AttachSpineFamily(Dictionary<string, string> assignedPaths, AssetIndex index)
    {
        var stemToBundle = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var kv in assignedPaths)
        {
            if (!LooksLikeSpineAsset(kv.Key) && !kv.Key.EndsWith(".prefab", StringComparison.OrdinalIgnoreCase)
                && !kv.Key.EndsWith(".json", StringComparison.OrdinalIgnoreCase)
                && !kv.Key.EndsWith(".png", StringComparison.OrdinalIgnoreCase))
                continue;
            string stem = SpineStem(kv.Key);
            if (string.IsNullOrEmpty(stem))
                continue;
            string folder = BundleFolderOf(kv.Key) ?? "";
            string key = folder + "/" + stem;
            if (!stemToBundle.ContainsKey(key))
                stemToBundle[key] = kv.Value;
        }

        if (stemToBundle.Count == 0)
            return;

        for (int i = 0; i < index.paths.Count; i++)
        {
            string path = index.paths[i];
            string stem = SpineStem(path);
            string bundle;
            if (string.IsNullOrEmpty(stem))
                continue;
            string folder = BundleFolderOf(path) ?? "";
            if (!stemToBundle.TryGetValue(folder + "/" + stem, out bundle))
                continue;
            if (LooksLikeSpineAsset(path)
                || Path.GetFileNameWithoutExtension(path).StartsWith(stem, StringComparison.OrdinalIgnoreCase))
            {
                AssignPathToBundle(path, bundle, assignedPaths);
            }
        }
    }

    static bool IsSpineAtlasTexture(string assetPath)
    {
        string file = Path.GetFileNameWithoutExtension(assetPath ?? "");
        string[] paths = AssetDatabase.GetAllAssetPaths();
        for (int i = 0; i < paths.Length; i++)
        {
            string other = paths[i];
            string name = Path.GetFileName(other);
            if (!name.EndsWith(".atlas.txt", StringComparison.OrdinalIgnoreCase)
                && !name.EndsWith(".atlas", StringComparison.OrdinalIgnoreCase)
                && !name.EndsWith("_Atlas.asset", StringComparison.OrdinalIgnoreCase))
                continue;
            string stem = SpineStem(other);
            if (file.Equals(stem, StringComparison.OrdinalIgnoreCase)
                || file.StartsWith(stem + "_", StringComparison.OrdinalIgnoreCase))
                return true;
        }

        return false;
    }

    static void ApplySpineTextureImporter(TextureImporter tex)
    {
        tex.textureType = TextureImporterType.Default;
        tex.alphaIsTransparency = false;
        tex.mipmapEnabled = false;
        tex.npotScale = TextureImporterNPOTScale.None;
        tex.wrapMode = TextureWrapMode.Clamp;
        tex.sRGBTexture = true;
    }

    static void ApplyAndroidTexture(string value)
    {
        if (string.IsNullOrEmpty(value))
            return;

        switch (value.Trim().ToUpperInvariant())
        {
            case "ASTC":
                EditorUserBuildSettings.androidBuildSubtarget = MobileTextureSubtarget.ASTC;
                break;
            case "ETC2":
                EditorUserBuildSettings.androidBuildSubtarget = MobileTextureSubtarget.ETC2;
                break;
            case "DXT":
                EditorUserBuildSettings.androidBuildSubtarget = MobileTextureSubtarget.DXT;
                break;
            default:
                Debug.LogWarning("AbRebuild: unknown -androidTexture " + value + ", leaving default");
                break;
        }
    }

    static void ApplyAndroidAssetCompression(string androidTexture, string astcBlockSize, string maxTextureSizeArg)
    {
        TextureImporterFormat format = ParseAndroidTextureFormat(androidTexture, astcBlockSize);
        int maxSize = 0;
        if (!string.IsNullOrEmpty(maxTextureSizeArg))
            int.TryParse(maxTextureSizeArg, out maxSize);

        AssetDatabase.StartAssetEditing();
        int textures = 0;
        int audios = 0;
        try
        {
            string[] paths = AssetDatabase.GetAllAssetPaths();
            for (int i = 0; i < paths.Length; i++)
            {
                string path = paths[i];
                if (IsForbiddenPath(path) || AssetDatabase.IsValidFolder(path))
                    continue;

                AssetImporter importer = AssetImporter.GetAtPath(path);
                TextureImporter tex = importer as TextureImporter;
                if (tex != null)
                {
                    bool spineTex = IsSpineAtlasTexture(path);
                    if (spineTex)
                        ApplySpineTextureImporter(tex);

                    TextureImporterPlatformSettings plat = tex.GetPlatformTextureSettings("Android");
                    plat.overridden = true;
                    plat.format = format;
                    plat.compressionQuality = spineTex ? 100 : 50;
                    if (maxSize > 0 && !spineTex)
                    {
                        int current = plat.maxTextureSize > 0 ? plat.maxTextureSize : tex.maxTextureSize;
                        plat.maxTextureSize = current > 0 ? Math.Min(current, maxSize) : maxSize;
                    }
                    tex.SetPlatformTextureSettings(plat);
                    textures++;
                    continue;
                }

                AudioImporter audio = importer as AudioImporter;
                if (audio != null)
                {
                    AudioImporterSampleSettings sample = audio.GetOverrideSampleSettings("Android");
                    sample.compressionFormat = AudioCompressionFormat.Vorbis;
                    sample.quality = 0.5f;
                    audio.SetOverrideSampleSettings("Android", sample);
                    audios++;
                }
            }
        }
        finally
        {
            AssetDatabase.StopAssetEditing();
        }

        Debug.Log("AbRebuild: Android compression overrides textures=" + textures + " audio=" + audios + " format=" + format);
    }

    static TextureImporterFormat ParseAndroidTextureFormat(string androidTexture, string astcBlockSize)
    {
        string kind = (androidTexture ?? "ASTC").Trim().ToUpperInvariant();
        if (kind == "ETC2")
            return TextureImporterFormat.ETC2_RGBA8;
        if (kind == "DXT")
            return TextureImporterFormat.DXT5;

        switch ((astcBlockSize ?? "8x8").Trim().ToLowerInvariant())
        {
            case "4x4":
                return TextureImporterFormat.ASTC_4x4;
            case "5x5":
                return TextureImporterFormat.ASTC_5x5;
            case "6x6":
                return TextureImporterFormat.ASTC_6x6;
            case "10x10":
                return TextureImporterFormat.ASTC_10x10;
            case "12x12":
                return TextureImporterFormat.ASTC_12x12;
            default:
                return TextureImporterFormat.ASTC_8x8;
        }
    }

    static BuildAssetBundleOptions ParseCompression(string value)
    {
        if (string.IsNullOrEmpty(value))
            return BuildAssetBundleOptions.None;

        switch (value.Trim().ToLowerInvariant())
        {
            case "lz4":
                return BuildAssetBundleOptions.ChunkBasedCompression;
            case "lzma":
                return BuildAssetBundleOptions.None;
            case "none":
                return BuildAssetBundleOptions.UncompressedAssetBundle;
            default:
                Debug.LogWarning("AbRebuild: unknown -compression " + value + ", using Unity default lzma");
                return BuildAssetBundleOptions.None;
        }
    }

    static string GetArg(string[] args, string name)
    {
        if (args == null)
            return null;
        for (int i = 0; i < args.Length; i++)
        {
            if (!string.Equals(args[i], name, StringComparison.OrdinalIgnoreCase))
                continue;
            if (i + 1 < args.Length && !args[i + 1].StartsWith("-"))
                return args[i + 1];
            return "";
        }

        return null;
    }

    static void FailAndMaybeExit(string message)
    {
        Debug.LogError("AbRebuild: " + message);
        if (Application.isBatchMode)
            EditorApplication.Exit(1);
    }

    class AssetIndex
    {
        public List<string> paths = new List<string>();
        public Dictionary<string, string> byExact = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        public Dictionary<string, List<string>> byNorm = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);
        public Dictionary<string, List<string>> byFile = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);
    }

    class AssignResult
    {
        public int assigned;
        public int mappingAssetCount;
        public List<AbFlatItem> missing = new List<AbFlatItem>();
    }

    [Serializable]
    public class AbFlatMapping
    {
        public AbFlatItem[] items;
    }

    [Serializable]
    public class AbFlatItem
    {
        public string bundle;
        public string name;
        public string type;
        public string path;
    }
}
