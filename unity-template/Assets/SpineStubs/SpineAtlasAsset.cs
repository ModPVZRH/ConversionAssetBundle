using UnityEngine;

namespace Spine.Unity
{
    public class SpineAtlasAsset : ScriptableObject
    {
        public int textureLoadingMode;
        public ScriptableObject onDemandTextureLoader;
        public TextAsset atlasFile;
        public Material[] materials;
    }
}
