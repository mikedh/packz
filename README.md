# packz

This is an experiment (i.e. not a real thing, please don't use) on basic "tree shaking" for packaging Python applications into an archive suitable for AWS Lambda or Cloudflare workers that includes *only* the exact files needed to run a specific application. 

The basic premise is this:
- `sys.settrace` records every *Python* file executed
- `lsof` lists *every other* file accessed, including compiled library `.so` files.

Combining those two data sources gives us a list of every file actually accessed so we can potentially produce archives that are small enough to be run as cloud functions. Eventually if this worked you'd run this in a Docker image for your target (i.e. `lambci/lambda:build-python3.8`). 


## Example
```
    # blacklist modules and specific file patterns for big things
    runner = PackRunner(
        mod_blacklist=['fcl'],
        file_blacklist=['*assimp*'])

    # start recording traces
    runner.start()

    # imports MUST be included inside the block!
    import app
    r = app.do()

    # stop recording data
    runner.stop()
    # copy the files to a build folder
    runner.copy(build_path='~/packz_build')
```