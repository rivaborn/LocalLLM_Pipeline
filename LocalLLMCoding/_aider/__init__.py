"""Internal package backing run_aider.py.

run_aider.py is a thin CLI shim; all real logic lives here, split into
single-responsibility submodules:

    env_config  -- .env discovery + endpoint/model resolution
    parser      -- aidercommands.md step parsing + file-list extraction
    prompts     -- ctags / pyright / step-plan prompt blocks
    verify      -- post-aider output sanity checks
    runner      -- build + invoke the aider subprocess per step
    cli         -- argparse wiring + main()
"""
