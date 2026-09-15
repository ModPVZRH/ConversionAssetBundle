using System;
using UnityEngine;

namespace Spine.Unity
{
    [Serializable]
    public class MaskMaterials
    {
        public Material[] materialsMaskDisabled;
        public Material[] materialsInsideMask;
        public Material[] materialsOutsideMask;
    }

    [Serializable]
    public class MecanimTranslator
    {
        public bool autoReset = true;
        public bool useCustomMixMode = true;
        public int[] layerMixModes;
        public int[] layerBlendModes;
    }

    [RequireComponent(typeof(MeshRenderer), typeof(MeshFilter))]
    public class SkeletonMecanim : MonoBehaviour
    {
        public SkeletonDataAsset skeletonDataAsset;
        public string initialSkinName;
        public bool initialFlipX;
        public bool initialFlipY;
        public int updateWhenInvisible = 3;
        public string[] separatorSlotNames;
        public float zSpacing;
        public bool useClipping = true;
        public bool immutableTriangles;
        public bool pmaVertexColors = true;
        public bool clearStateOnDisable;
        public bool tintBlack;
        public bool singleSubmesh;
        public bool fixDrawOrder;
        public bool addNormals;
        public bool calculateTangents;
        public int maskInteraction;
        public MaskMaterials maskMaterials = new MaskMaterials();
        public bool disableRenderingOnOverride = true;
        public Vector2 physicsPositionInheritanceFactor = Vector2.one;
        public float physicsRotationInheritanceFactor = 1f;
        public Transform physicsMovementRelativeTo;
        public MecanimTranslator translator = new MecanimTranslator();
        public int updateTiming = 1;
    }
}
