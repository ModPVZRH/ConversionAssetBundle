using System;
using UnityEngine;

namespace Spine.Unity
{
    [Serializable]
    public class BlendModeMaterials
    {
        public bool requiresBlendModeMaterials;
        public bool applyAdditiveMaterial;
        public Material[] additiveMaterials;
        public Material[] multiplyMaterials;
        public Material[] screenMaterials;
    }

    public class SkeletonDataAsset : ScriptableObject
    {
        public SpineAtlasAsset[] atlasAssets;
        public float scale = 0.01f;
        public TextAsset skeletonJSON;
        public bool isUpgradingBlendModeMaterials;
        public BlendModeMaterials blendModeMaterials = new BlendModeMaterials();
        public ScriptableObject[] skeletonDataModifiers;
        public string[] fromAnimation;
        public string[] toAnimation;
        public float[] duration;
        public float defaultMix = 0.2f;
        public RuntimeAnimatorController controller;
    }
}
