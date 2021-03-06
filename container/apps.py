import hpccm


import config


class Gromacs:
    _os_packages = ['wget']
    _cmake_opts = "\
                -DCMAKE_INSTALL_BINDIR=bin.$simd$ \
                -DCMAKE_INSTALL_LIBDIR=lib.$simd$ \
                -DCMAKE_C_COMPILER=$c_compiler$ \
                -DCMAKE_CXX_COMPILER=$cxx_compiler$ \
                -DGMX_OPENMP=ON \
                -DGMX_MPI=$mpi$ \
                -DGMX_GPU=$cuda$ \
                -DGMX_SIMD=$simd$ \
                -DGMX_USE_RDTSCP=$rdtscp$ \
                -DGMX_DOUBLE=$double$ \
                -D$fft$ \
                -DGMX_EXTERNAL_BLAS=OFF \
                -DGMX_EXTERNAL_LAPACK=OFF \
                -DBUILD_SHARED_LIBS=OFF \
                -DGMX_PREFER_STATIC_LIBS=ON \
                -DREGRESSIONTEST_DOWNLOAD=$regtest$ \
                -DGMX_DEFAULT_SUFFIX=OFF \
                -DGMX_BINARY_SUFFIX=$bin_suffix$ \
                -DGMX_LIBS_SUFFIX=$libs_suffix$ \
                "

    def __init__(self, *, stage_name, base_image, args, building_blocks, previous_stages):
        self.stage = hpccm.Stage()
        self.base_image = base_image
        self.previous_stages = previous_stages
        # The following two will be required in generic_cmake
        self.preconfigure = []
        self.check = False

        self.__prepare(stage_name=stage_name, building_blocks=building_blocks)
        self.__gromacs(args=args, building_blocks=building_blocks)
        self.__regtest(args=args)
        self.__add__engines(args=args, building_blocks=building_blocks)

    def __prepare(self, *, stage_name, building_blocks):
        '''
        Prepare the stage
        '''
        self.stage += hpccm.primitives.baseimage(image=self.base_image, _as=stage_name)
        self.stage += hpccm.building_blocks.packages(ospackages=self._os_packages)
        for bb in ('compiler', 'cmake'):
            if building_blocks.get(bb, None) is not None:
                self.stage += building_blocks[bb]

        # adding runtime for fftw from dev stage
        if self.previous_stages.get('dev', None) is not None:
            if building_blocks.get('fftw', None) is not None:
                self.stage += building_blocks['fftw'].runtime(_from='dev')

            if building_blocks.get('mpi', None) is not None:
                self.stage += building_blocks['mpi'].runtime(_from='dev')

    def __gromacs(self, *, args, building_blocks):
        '''
        Feed the stage with GROMACS related stuff
        '''
        self.stage += hpccm.primitives.label(metadata={'gromacs.version': args.gromacs})
        # relative to /var/tmp
        self.source_directory = 'gromacs-{version}'.format(version=args.gromacs)
        # relative to source_directory
        self.build_directory = 'build.{simd}'
        # installation directotry
        self.prefix = config.GMX_INSTALLATION_DIRECTORY
        # environment variables to be set prior to Gromacs build
        self.build_environment = {}
        # url to download Gromacs
        self.url = 'ftp://ftp.gromacs.org/pub/gromacs/gromacs-{version}.tar.gz'.format(version=args.gromacs)

        self.gromacs_cmake_opts = self.__get_gromacs_cmake_opts(args=args,
                                                                building_blocks=building_blocks)
        self.wrapper = 'gmx' + self.__get_wrapper_suffix(args=args,
                                                         building_blocks=building_blocks)

    def __regtest(self, *, args):
        # TODO: We may try with installing perl in using ospackages primitives
        if args.regtest:
            # preinstall
            self.preconfigure = ['apt-get update',
                                 'apt-get upgrade -y',
                                 'apt-get install -y perl', ]
            # allow regression test
            self.check = True

    def __add__engines(self, *, args, building_blocks):
        '''
        Adding GROMACS engine the container
        '''
        # We dont want to use the same engine multiple times
        for engine in set(args.engines):
            # binary and library suffix for gmx
            parsed_engine = self.__parse_engine(engine)
            bin_libs_suffix = self.__get_bin_libs_suffix(parsed_engine['rdtscp'],
                                                         args=args,
                                                         building_blocks=building_blocks)
            engine_cmake_opts = self.gromacs_cmake_opts.replace('$bin_suffix$', bin_libs_suffix)
            engine_cmake_opts = engine_cmake_opts.replace('$libs_suffix$', bin_libs_suffix)

            # simd, rdtscp
            for key in parsed_engine:
                value = parsed_engine[key] if key == 'simd' else parsed_engine[key].upper()
                engine_cmake_opts = engine_cmake_opts.replace('$' + key + '$', value)

            self.stage += hpccm.building_blocks.generic_cmake(cmake_opts=engine_cmake_opts.split(),
                                                              directory=self.source_directory,
                                                              build_directory=self.build_directory.format(simd=parsed_engine['simd']),
                                                              prefix=self.prefix,
                                                              build_environment=self.build_environment,
                                                              url=self.url,
                                                              preconfigure=self.preconfigure,
                                                              check=self.check)

    def __parse_engine(self, engine):
        '''
        Parsing engine's value
        '''
        if engine:
            engine_args = map(lambda x: x.strip(), engine.split(':'))
            engine_args_dict = {}
            for engine_arg in engine_args:
                key, value = map(lambda x: x.strip(), engine_arg.split('='))

                # Check engine argument and value
                self.__check_engine_argument(key=key, value=value)

                engine_args_dict[key] = config.SIMD_MAPPER[value] if key == 'simd' else value
            return engine_args_dict

    def __check_engine_argument(self, *, key, value):
        '''
        Check whether a value is missing in engines option
        '''
        if not key in config.ENGINE_OPTIONS.keys():
            raise KeyError('{key} not valid engine key. Available keys are {keys}'.format(
                key=key,
                keys=config.ENGINE_OPTIONS['simd'])
            )
        else:
            if not value in config.ENGINE_OPTIONS[key]:
                raise ValueError('{value} is not valid value for key "{key}". Available values are : {values}'.format(
                    value=value, key=key, values=config.ENGINE_OPTIONS[key])
                )

    def __get_gromacs_cmake_opts(self, *, args, building_blocks):
        '''
        Configure the common cmake_opts for different Gromacs build
        based on sind instruction
        '''
        gromacs_cmake_opts = self._cmake_opts[:]

        # Compiler and mpi
        if building_blocks.get('mpi', None) is not None:
            gromacs_cmake_opts = gromacs_cmake_opts.replace('$c_compiler$', 'mpicc')
            gromacs_cmake_opts = gromacs_cmake_opts.replace('$cxx_compiler$', 'mpicxx')
            gromacs_cmake_opts = gromacs_cmake_opts.replace('$mpi$', 'ON')
            gromacs_cmake_opts = gromacs_cmake_opts + " -DMPIEXEC_PREFLAGS='--allow-run-as-root;--oversubscribe'"
        else:
            gromacs_cmake_opts = gromacs_cmake_opts.replace('$c_compiler$', 'gcc')
            gromacs_cmake_opts = gromacs_cmake_opts.replace('$cxx_compiler$', 'g++')
            gromacs_cmake_opts = gromacs_cmake_opts.replace('$mpi$', 'OFF')

        #  fftw
        if building_blocks.get('fftw', None) is not None:
            gromacs_cmake_opts = gromacs_cmake_opts.replace('$fft$', 'GMX_FFT_LIBRARY=fftw3')
            self.build_environment['CMAKE_PREFIX_PATH'] = '\'/usr/local/fftw\''
        else:
            gromacs_cmake_opts = gromacs_cmake_opts.replace('$fft$', 'GMX_BUILD_OWN_FFTW=ON')

        # cuda, regtest, double
        for (option, enabled) in zip(['cuda', 'regtest', 'double'], [args.cuda, args.regtest, args.double]):
            if enabled:
                gromacs_cmake_opts = gromacs_cmake_opts.replace('$' + option + '$', 'ON')
            else:
                gromacs_cmake_opts = gromacs_cmake_opts.replace('$' + option + '$', 'OFF')

        return gromacs_cmake_opts

    def __get_wrapper_suffix(self, *, args, building_blocks):
        '''
        Set the wrapper suffix based on mpi enabled/disabled and
        double precision enabled and disabled
        '''
        return config.WRAPPER_SUFFIX_FORMAT.format(
            mpi=config.GMX_ENGINE_SUFFIX_OPTIONS['mpi'] if building_blocks.get('mpi', None) is not None else '',
            double=config.GMX_ENGINE_SUFFIX_OPTIONS['double'] if args.double else ''
        )

    def __get_bin_libs_suffix(self, rdtscp, *, args, building_blocks):
        '''
        Set tgmx binaries and library suffix based on mpi enabled/disabled,
        double precision enabled and disabled and
        rdtscp enabled/disabled
        '''
        return config.BINARY_SUFFIX_FORMAT.format(mpi=config.GMX_ENGINE_SUFFIX_OPTIONS['mpi'] if building_blocks.get('mpi', None) is not None else '',
                                                  double=config.GMX_ENGINE_SUFFIX_OPTIONS['double'] if args.double else '',
                                                  rdtscp=config.GMX_ENGINE_SUFFIX_OPTIONS['rdtscp'] if rdtscp.lower() == 'on' else '')

    def __call__(self):
        '''
        Return the stage and the name of the wrapper binaries
        '''
        return (self.stage, self.wrapper)
